#include "vehicle_workbench_axles/runtime.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#ifndef VWA_RUNTIME_VERSION
#define VWA_RUNTIME_VERSION "4.4.0"
#endif

namespace vwa {

namespace {

constexpr std::uint16_t kManagedWheelBits =
    static_cast<std::uint16_t>(kSteeredBit | kDrivenBit);
constexpr double kGainComparisonEpsilon = kSteeringGainEpsilon;
constexpr double kStaticForceAbsoluteTolerance = 1.0e-4;
constexpr double kStaticForceRelativeTolerance = 0.005;

bool StaticForceEqual(double left, double right) noexcept {
    const double scale = std::max(std::abs(left), std::abs(right));
    const double tolerance = std::max(
        kStaticForceAbsoluteTolerance,
        kStaticForceRelativeTolerance * scale);
    return std::abs(left - right) <= tolerance;
}

double BaseSteeringGain(const AxleDefinition& axle) noexcept {
    return axle.steering_gain.value_or(axle.steered ? 1.0 : 0.0);
}

double EffectiveSteeringGain(const AxleConfiguration& configuration,
                             const AxleDefinition& axle) noexcept {
    const double polarity =
        configuration.steering_command_polarity == "inverted" ? -1.0 : 1.0;
    return BaseSteeringGain(axle) * polarity;
}

double EffectiveSteeringGain(const AxleConfiguration& configuration,
                             double base_gain) noexcept {
    const double polarity =
        configuration.steering_command_polarity == "inverted" ? -1.0 : 1.0;
    return base_gain * polarity;
}

bool RequestsRuntimeGeometry(
    const AxleConfiguration& configuration) noexcept {
    return configuration.steering_calculation.has_value() &&
           configuration.steering_calculation->mode == "automaticGeometry" &&
           configuration.steering_calculation->runtime_recompute;
}

bool RequiresSteeringGainAccess(
    const AxleConfiguration& configuration) noexcept {
    if (RequestsRuntimeGeometry(configuration)) return true;
    return std::any_of(
        configuration.axles.begin(), configuration.axles.end(),
        [&configuration](const AxleDefinition& axle) {
            const double legacy_gain = axle.steered ? 1.0 : 0.0;
            return std::abs(
                       EffectiveSteeringGain(configuration, axle) - legacy_gain) >
                   kGainComparisonEpsilon;
        });
}

bool RequiresStaticForceAccess(
    const AxleConfiguration& configuration) noexcept {
    return !configuration.axles.empty() && std::all_of(
        configuration.axles.begin(), configuration.axles.end(),
        [](const AxleDefinition& axle) {
            return axle.suspension.has_value();
        });
}

bool HasAnyStaticForceSetting(
    const AxleConfiguration& configuration) noexcept {
    return std::any_of(
        configuration.axles.begin(), configuration.axles.end(),
        [](const AxleDefinition& axle) {
            return axle.suspension.has_value();
        });
}

std::optional<std::int32_t>
CanonicalWheelBoneId(const std::string& bone) noexcept {
    if (bone == "wheel_lf") return 11;
    if (bone == "wheel_rf") return 12;
    if (bone == "wheel_lr") return 13;
    if (bone == "wheel_rr") return 14;
    if (bone == "wheel_lm1") return 15;
    if (bone == "wheel_rm1") return 16;
    if (bone == "wheel_lm2") return 17;
    if (bone == "wheel_rm2") return 18;
    if (bone == "wheel_lm3") return 19;
    if (bone == "wheel_rm3") return 20;
    return std::nullopt;
}

std::string HashText(std::uint32_t value) {
    std::ostringstream output;
    output << "0x" << std::uppercase << std::hex << std::setw(8)
           << std::setfill('0') << value;
    return output.str();
}

const char* EventText(VehicleEvent event) noexcept {
    switch (event) {
    case VehicleEvent::Created: return "created";
    case VehicleEvent::OwnershipChanged: return "ownership-changed";
    case VehicleEvent::Repaired: return "repaired";
    case VehicleEvent::WheelStateRecreated: return "wheel-state-recreated";
    }
    return "unknown";
}

std::string SafeSourceName(const std::string& value) {
    const auto separator = value.find_last_of("/\\");
    return separator == std::string::npos ? value : value.substr(separator + 1U);
}

} // namespace

struct AxleRuntime::Implementation {
    struct OriginalWheelState {
        std::uint16_t flags{0};
        double steering_gain{0.0};
        double desired_steering_gain{0.0};
        bool has_steering_gain{false};
        double static_force{0.0};
        bool has_static_force{false};
    };

    struct TrackedVehicle {
        std::uint32_t model_hash{0};
        std::uint64_t wheel_generation{0};
        std::map<std::uint32_t, OriginalWheelState> originals;
        std::chrono::steady_clock::time_point last_verified{};
    };

    struct WheelPlan {
        std::uint32_t index{0};
        bool steered{false};
        bool powered{false};
        double desired_steering_gain{0.0};
        std::uint16_t original_flags{0};
        std::uint16_t desired_flags{0};
        double original_steering_gain{0.0};
        double support_weight{1.0};
        double original_static_force{0.0};
        double desired_static_force{0.0};
        bool flags_changed{false};
        bool steering_gain_changed{false};
        bool manages_steering_gain{false};
        bool static_force_changed{false};
        bool manages_static_force{false};
    };

    IVehicleHost& host;
    IWheelAccess& access;
    ILogSink& log;
    RuntimeSettings settings;
    RuntimeState state{RuntimeState::Stopped};
    std::unordered_map<std::uint32_t, AxleConfiguration> configurations;
    std::unordered_map<std::uint64_t, TrackedVehicle> tracked;
    std::unordered_set<std::string> emitted_once;
    std::chrono::steady_clock::time_point last_discovery{};

    Implementation(IVehicleHost& host_ref, IWheelAccess& access_ref,
                   ILogSink& log_ref, RuntimeSettings runtime_settings)
        : host(host_ref), access(access_ref), log(log_ref),
          settings(std::move(runtime_settings)) {}

    void LogOnce(LogLevel level, const std::string& code,
                 const std::string& message) {
        if (emitted_once.insert(code + "\n" + message).second) {
            log.Write(level, code, message);
        }
    }

    std::optional<std::uint64_t>
    ResolveWheelGeneration(const VehicleSnapshot& vehicle) {
        if (!access.SupportsWheelGenerationToken()) {
            return vehicle.wheel_generation;
        }
        std::uint64_t generation = 0;
        if (!access.ReadWheelGenerationToken(vehicle, generation) ||
            generation == 0) {
            LogOnce(LogLevel::Warning, "wheel-generation-unavailable",
                    "Live wheel storage could not be fingerprinted for model " +
                        HashText(vehicle.model_hash) +
                        "; lifecycle-sensitive reads and writes were skipped");
            return std::nullopt;
        }
        return generation;
    }

    bool EditionAllowed(const AxleConfiguration& configuration,
                        Edition edition) const noexcept {
        return (edition == Edition::Legacy && configuration.story_legacy) ||
               (edition == Edition::Enhanced && configuration.story_enhanced);
    }

    std::optional<std::map<std::uint32_t, double>>
    ResolveBaseSteeringGains(
        const VehicleSnapshot& vehicle,
        const AxleConfiguration& configuration) {
        std::map<std::uint32_t, double> result;
        for (const auto& axle : configuration.axles) {
            result.emplace(axle.order, BaseSteeringGain(axle));
        }
        if (!RequestsRuntimeGeometry(configuration)) return result;

        if (!access.SupportsWheelLocalPosition()) {
            LogOnce(LogLevel::Error,
                    "wheel-local-position-capability-missing",
                    "Model " + HashText(configuration.model_hash) +
                        " requests runtime steering geometry, but this exact "
                        "build profile cannot read authoritative vehicle-local "
                        "wheel positions; no changes were applied");
            return std::nullopt;
        }

        const auto& evidence = *configuration.steering_calculation;
        const double pair_tolerance = *evidence.pair_position_tolerance;
        const double epsilon = *evidence.position_epsilon;
        std::map<std::uint32_t, double> axle_positions;
        for (const auto& axle : configuration.axles) {
            const auto left_mapping =
                configuration.wheel_index_map.find(axle.left_bone);
            const auto right_mapping =
                configuration.wheel_index_map.find(axle.right_bone);
            if (left_mapping == configuration.wheel_index_map.end() ||
                right_mapping == configuration.wheel_index_map.end()) {
                LogOnce(LogLevel::Error, "runtime-geometry-wheel-map-invalid",
                        "Runtime steering geometry could not resolve every "
                        "configured wheel index; no changes were applied");
                return std::nullopt;
            }
            WheelLocalPosition left;
            WheelLocalPosition right;
            if (!access.ReadWheelLocalPosition(
                    vehicle, left_mapping->second, left) ||
                !access.ReadWheelLocalPosition(
                    vehicle, right_mapping->second, right) ||
                !std::isfinite(left.lateral) ||
                !std::isfinite(left.longitudinal) ||
                !std::isfinite(left.vertical) ||
                !std::isfinite(right.lateral) ||
                !std::isfinite(right.longitudinal) ||
                !std::isfinite(right.vertical)) {
                LogOnce(LogLevel::Error,
                        "runtime-wheel-position-read-failed",
                        "Authoritative vehicle-local wheel positions could not "
                        "be read safely for model " +
                            HashText(configuration.model_hash) +
                            "; no changes were applied");
                return std::nullopt;
            }
            if (std::abs(left.longitudinal - right.longitudinal) >
                pair_tolerance) {
                LogOnce(LogLevel::Error,
                        "runtime-wheel-pair-position-mismatch",
                        "Left and right wheel positions exceed the authored "
                        "pair tolerance for model " +
                            HashText(configuration.model_hash) +
                            "; no changes were applied");
                return std::nullopt;
            }
            axle_positions.emplace(
                axle.order,
                (left.longitudinal + right.longitudinal) * 0.5);
        }

        const double first_position =
            axle_positions.at(configuration.axles.front().order);
        const double last_position =
            axle_positions.at(configuration.axles.back().order);
        if (std::abs(first_position - last_position) <= epsilon) {
            LogOnce(LogLevel::Error, "runtime-geometry-axis-degenerate",
                    "Runtime wheel positions do not establish a longitudinal "
                    "front-to-rear axis; no changes were applied");
            return std::nullopt;
        }
        const double forward_axis =
            first_position > last_position ? 1.0 : -1.0;

        double pivot = *evidence.pivot_longitudinal_position;
        if (evidence.pivot_source != "explicit") {
            double total = 0.0;
            for (const auto order : evidence.pivot_axle_orders) {
                const auto found = axle_positions.find(order);
                if (found == axle_positions.end()) {
                    LogOnce(LogLevel::Error,
                            "runtime-geometry-pivot-invalid",
                            "Runtime steering pivot references an unavailable "
                            "physical axle; no changes were applied");
                    return std::nullopt;
                }
                total += found->second;
            }
            pivot = total /
                    static_cast<double>(evidence.pivot_axle_orders.size());
        }

        const AxleDefinition* reference = nullptr;
        double reference_distance = -1.0;
        double reference_forward_offset = 0.0;
        for (const auto& axle : configuration.axles) {
            if (!axle.steered) continue;
            const double offset = axle_positions.at(axle.order) - pivot;
            const double distance = std::abs(offset);
            const double forward_offset = forward_axis * offset;
            if (distance <= epsilon) {
                LogOnce(LogLevel::Error,
                        "runtime-geometry-steered-at-pivot",
                        "A steered physical axle coincides with the runtime "
                        "steering pivot; no changes were applied");
                return std::nullopt;
            }
            if (reference == nullptr ||
                distance > reference_distance + epsilon ||
                (std::abs(distance - reference_distance) <= epsilon &&
                 forward_offset > reference_forward_offset)) {
                reference = &axle;
                reference_distance = distance;
                reference_forward_offset = forward_offset;
            }
        }
        if (reference == nullptr) {
            LogOnce(LogLevel::Error, "runtime-geometry-no-steered-axle",
                    "Runtime steering geometry has no usable steered axle; no "
                    "changes were applied");
            return std::nullopt;
        }

        const double pi = std::acos(-1.0);
        const double lock_radians =
            *evidence.reference_lock_degrees * pi / 180.0;
        const double turn_radius =
            reference_distance / std::tan(lock_radians);
        if (!std::isfinite(turn_radius) || turn_radius <= epsilon) {
            LogOnce(LogLevel::Error, "runtime-geometry-radius-invalid",
                    "Runtime steering geometry could not establish a stable "
                    "turn radius; no changes were applied");
            return std::nullopt;
        }

        bool corrected_exported_gain = false;
        for (const auto& axle : configuration.axles) {
            double gain = 0.0;
            if (axle.steered) {
                const double offset = axle_positions.at(axle.order) - pivot;
                gain = std::atan(forward_axis * offset / turn_radius) /
                       lock_radians;
                if (!std::isfinite(gain) || std::abs(gain) > 1.0 + epsilon) {
                    LogOnce(LogLevel::Error,
                            "runtime-geometry-gain-invalid",
                            "Runtime steering geometry produced an unsafe gain; "
                            "no changes were applied");
                    return std::nullopt;
                }
                gain = std::max(-1.0, std::min(1.0, gain));
                if (std::abs(gain) <= epsilon) gain = 0.0;
            }
            corrected_exported_gain = corrected_exported_gain ||
                std::abs(gain - BaseSteeringGain(axle)) >
                    kGainComparisonEpsilon;
            result[axle.order] = gain;
        }
        LogOnce(LogLevel::Info, "runtime-steering-geometry-resolved",
                "Model " + HashText(configuration.model_hash) +
                    " recalculated steering from authoritative vehicle-local "
                    "wheel positions using physical axle " +
                    std::to_string(reference->order) +
                    " as the farthest steering reference");
        if (corrected_exported_gain) {
            LogOnce(LogLevel::Warning,
                    "runtime-steering-geometry-corrected",
                    "Runtime wheel positions changed one or more exported "
                    "steering gains for model " +
                        HashText(configuration.model_hash));
        }
        return result;
    }

    std::optional<std::vector<WheelPlan>>
    BuildPlan(const VehicleSnapshot& vehicle,
              const AxleConfiguration& configuration,
              std::uint32_t game_wheel_count) {
        if (game_wheel_count != configuration.expected_wheel_count) {
            LogOnce(LogLevel::Warning, "wheel-count-mismatch",
                    "Model " + HashText(configuration.model_hash) +
                        " expected " +
                        std::to_string(configuration.expected_wheel_count) +
                        " wheels but the game reported " +
                        std::to_string(game_wheel_count) +
                        "; configuration skipped");
            return std::nullopt;
        }
        if (access.SupportsWheelBoneId()) {
            for (const auto& [bone, index] : configuration.wheel_index_map) {
                const auto expected = CanonicalWheelBoneId(bone);
                std::int32_t actual = -1;
                if (!expected.has_value() || index >= game_wheel_count ||
                    !access.ReadWheelBoneId(vehicle, index, actual) ||
                    actual != *expected) {
                    LogOnce(LogLevel::Error, "runtime-wheel-bone-mismatch",
                            "Model " + HashText(configuration.model_hash) +
                                " maps " + bone + " to collection slot " +
                                std::to_string(index) +
                                ", but the live CWheel bone ID does not match; "
                                "no changes were applied");
                    return std::nullopt;
                }
            }
        }

        std::vector<WheelPlan> plan;
        plan.reserve(configuration.axles.size() * 2U);
        const bool manage_steering_gain =
            RequiresSteeringGainAccess(configuration);
        const bool manage_static_force =
            RequiresStaticForceAccess(configuration);
        if ((configuration.schema_version == kAxleSupportAxleSchemaVersion ||
             HasAnyStaticForceSetting(configuration)) &&
            !manage_static_force) {
            LogOnce(LogLevel::Error, "incomplete-support-bias",
                    "Model " + HashText(configuration.model_hash) +
                        " does not define supportWeight for every physical "
                        "axle; no changes were applied");
            return std::nullopt;
        }
        if (manage_steering_gain && !access.SupportsSteeringGain()) {
            LogOnce(LogLevel::Error, "steering-gain-capability-missing",
                    "Model " + HashText(configuration.model_hash) +
                        " requests signed or scaled steering gain, but the "
                        "validated build profile exposes steering flags only; "
                        "no changes were applied");
            return std::nullopt;
        }
        if (manage_static_force &&
            (!access.SupportsStaticForce() ||
             !host.SupportsPhysicsActivation())) {
            LogOnce(LogLevel::Error, "static-force-capability-missing",
                    "Model " + HashText(configuration.model_hash) +
                        " requests axle support bias, but this exact build "
                        "does not expose validated StaticForce access and "
                        "physics activation; no changes were applied");
            return std::nullopt;
        }
        const auto base_steering_gains =
            ResolveBaseSteeringGains(vehicle, configuration);
        if (!base_steering_gains.has_value()) return std::nullopt;
        std::set<std::uint32_t> unique_indices;
        for (const auto& axle : configuration.axles) {
            for (const auto* bone : {&axle.left_bone, &axle.right_bone}) {
                const auto mapping = configuration.wheel_index_map.find(*bone);
                if (mapping == configuration.wheel_index_map.end() ||
                    mapping->second >= game_wheel_count ||
                    !unique_indices.insert(mapping->second).second) {
                    LogOnce(LogLevel::Error, "runtime-wheel-map-invalid",
                            "Model " + HashText(configuration.model_hash) +
                                " has a missing, duplicate, or out-of-range "
                                "runtime wheel mapping; no changes were applied");
                    return std::nullopt;
                }
                WheelPlan wheel;
                wheel.index = mapping->second;
                wheel.steered = axle.steered;
                wheel.powered = axle.powered;
                wheel.desired_steering_gain =
                    EffectiveSteeringGain(
                        configuration,
                        base_steering_gains->at(axle.order));
                // GTA owns a volatile gain field even for fixed wheels.  Do
                // not interpret its ordinary updates as drift and never write
                // gain state for an axle which is explicitly non-steering.
                wheel.manages_steering_gain =
                    manage_steering_gain && axle.steered;
                wheel.manages_static_force = manage_static_force;
                if (manage_static_force) {
                    wheel.support_weight =
                        axle.suspension->support_weight;
                }
                plan.push_back(wheel);
            }
        }
        if (plan.size() != game_wheel_count) {
            LogOnce(LogLevel::Error, "runtime-wheel-map-incomplete",
                    "Model " + HashText(configuration.model_hash) +
                        " does not map every game-reported physical wheel; "
                        "cosmetic dual tyres must not be added to wheelIndexMap");
            return std::nullopt;
        }

        std::sort(plan.begin(), plan.end(), [](const WheelPlan& left,
                                               const WheelPlan& right) {
            return left.index < right.index;
        });
        double original_static_force_total = 0.0;
        double weighted_static_force_total = 0.0;
        for (auto& wheel : plan) {
            if (!access.ReadWheelFlags(vehicle, wheel.index,
                                       wheel.original_flags)) {
                LogOnce(LogLevel::Error, "wheel-read-failed",
                        "Wheel flags could not be read safely; model " +
                            HashText(configuration.model_hash) + " was skipped");
                return std::nullopt;
            }
            if (wheel.manages_static_force &&
                (!access.ReadWheelStaticForce(
                     vehicle, wheel.index, wheel.original_static_force) ||
                 !std::isfinite(wheel.original_static_force) ||
                 wheel.original_static_force <= 0.0)) {
                LogOnce(LogLevel::Error, "static-force-read-failed",
                        "Suspension StaticForce could not be read safely; model " +
                            HashText(configuration.model_hash) +
                            " was skipped with no writes");
                return std::nullopt;
            }
            if (wheel.manages_static_force) {
                original_static_force_total += wheel.original_static_force;
                weighted_static_force_total +=
                    wheel.original_static_force * wheel.support_weight;
            }
            if (wheel.manages_steering_gain &&
                (!access.ReadWheelSteeringGain(
                     vehicle, wheel.index, wheel.original_steering_gain) ||
                 !std::isfinite(wheel.original_steering_gain))) {
                LogOnce(LogLevel::Error, "steering-gain-read-failed",
                        "Steering gain could not be read safely; model " +
                            HashText(configuration.model_hash) +
                            " was skipped with no writes");
                return std::nullopt;
            }
            std::uint16_t desired = static_cast<std::uint16_t>(
                wheel.original_flags & static_cast<std::uint16_t>(~kManagedWheelBits));
            if (wheel.steered) {
                desired = static_cast<std::uint16_t>(desired | kSteeredBit);
            }
            if (wheel.powered) {
                desired = static_cast<std::uint16_t>(desired | kDrivenBit);
            }
            wheel.desired_flags = desired;
            wheel.flags_changed = desired != wheel.original_flags;
            wheel.steering_gain_changed =
                wheel.manages_steering_gain &&
                std::abs(wheel.desired_steering_gain -
                         wheel.original_steering_gain) >
                    kGainComparisonEpsilon;
        }
        if (manage_static_force) {
            if (!std::isfinite(original_static_force_total) ||
                original_static_force_total <= 0.0 ||
                !std::isfinite(weighted_static_force_total) ||
                weighted_static_force_total <= 0.0) {
                LogOnce(LogLevel::Error, "static-force-normalization-invalid",
                        "Original suspension support or authored support weights "
                        "could not be normalized; no changes were applied");
                return std::nullopt;
            }
            const double normalization =
                original_static_force_total / weighted_static_force_total;
            for (auto& wheel : plan) {
                wheel.desired_static_force =
                    wheel.original_static_force * wheel.support_weight *
                    normalization;
                if (!std::isfinite(wheel.desired_static_force) ||
                    wheel.desired_static_force <= 0.0) {
                    LogOnce(LogLevel::Error, "static-force-normalization-invalid",
                            "Normalized suspension support was not finite and positive; no changes were applied");
                    return std::nullopt;
                }
                wheel.static_force_changed = !StaticForceEqual(
                    wheel.desired_static_force,
                    wheel.original_static_force);
            }
        }
        return plan;
    }

    bool RollBack(const VehicleSnapshot& vehicle,
                  const std::vector<WheelPlan>& plan,
                  std::size_t changed_through) {
        bool complete = true;
        bool restored_static_force = false;
        for (std::size_t index = 0; index < changed_through; ++index) {
            const auto& wheel = plan[index];
            if (wheel.flags_changed) {
                if (!ConfirmOfflineBeforeWrite() ||
                    !access.WriteWheelFlags(vehicle, wheel.index,
                                            wheel.original_flags)) {
                    complete = false;
                    if (state == RuntimeState::DisabledOnline) return false;
                }
                const bool was_powered =
                    (wheel.original_flags & kDrivenBit) != 0;
                if (!ConfirmOfflineBeforeWrite() ||
                    !access.SetWheelPowered(vehicle, wheel.index,
                                            was_powered)) {
                    complete = false;
                    if (state == RuntimeState::DisabledOnline) return false;
                }
            }
            if (wheel.steering_gain_changed) {
                if (!ConfirmOfflineBeforeWrite() ||
                    !access.WriteWheelSteeringGain(
                        vehicle, wheel.index,
                        wheel.original_steering_gain)) {
                    complete = false;
                    if (state == RuntimeState::DisabledOnline) return false;
                }
            }
            if (wheel.static_force_changed) {
                if (!ConfirmOfflineBeforeWrite() ||
                    !access.WriteWheelStaticForce(
                        vehicle, wheel.index,
                        wheel.original_static_force)) {
                    complete = false;
                    if (state == RuntimeState::DisabledOnline) return false;
                } else {
                    restored_static_force = true;
                }
            }
        }
        if (restored_static_force) {
            if (!ConfirmOfflineBeforeWrite() ||
                !host.ActivatePhysics(vehicle)) {
                complete = false;
                if (state == RuntimeState::DisabledOnline) return false;
            }
            for (std::size_t index = 0; index < changed_through; ++index) {
                const auto& wheel = plan[index];
                if (!wheel.static_force_changed) continue;
                double verified = 0.0;
                if (!access.ReadWheelStaticForce(
                        vehicle, wheel.index, verified) ||
                    !std::isfinite(verified) ||
                    !StaticForceEqual(
                        verified, wheel.original_static_force)) {
                    complete = false;
                }
            }
        }
        return complete;
    }

    bool Apply(const VehicleSnapshot& vehicle,
               const AxleConfiguration& configuration,
               std::chrono::steady_clock::time_point now,
               bool event_forced,
               const char* reason) {
        if (host.IsOnlineSession()) {
            DisableForOnline();
            return false;
        }

        std::uint32_t game_wheel_count = 0;
        if (!access.GetWheelCount(vehicle, game_wheel_count) ||
            game_wheel_count == 0 || game_wheel_count > 10) {
            LogOnce(LogLevel::Error, "wheel-count-unavailable",
                    "Game wheel count could not be validated for model " +
                        HashText(configuration.model_hash));
            return false;
        }

        const auto generation_before = ResolveWheelGeneration(vehicle);
        if (!generation_before.has_value()) return false;

        auto plan = BuildPlan(vehicle, configuration, game_wheel_count);
        if (!plan.has_value()) return false;

        const auto generation_after = ResolveWheelGeneration(vehicle);
        if (!generation_after.has_value()) return false;
        if (*generation_after != *generation_before) {
            LogOnce(LogLevel::Warning, "wheel-generation-changed-during-plan",
                    "Wheel storage changed while planning model " +
                        HashText(configuration.model_hash) +
                        "; the stale plan was discarded with no writes");
            return false;
        }

        // Reads and plan construction can take an arbitrary amount of time.
        // Re-check the session at the write boundary, then again before each
        // individual mutating adapter call below.
        if (!ConfirmOfflineBeforeWrite()) return false;

        auto tracked_it = tracked.find(vehicle.entity_id);
        const bool new_generation =
            tracked_it == tracked.end() ||
            tracked_it->second.wheel_generation != *generation_after ||
            tracked_it->second.model_hash != vehicle.model_hash;
        if (!new_generation && !plan->empty() &&
            plan->front().manages_static_force) {
            double baseline_total = 0.0;
            double weighted_baseline_total = 0.0;
            bool baseline_complete = true;
            for (const auto& wheel : *plan) {
                const auto original =
                    tracked_it->second.originals.find(wheel.index);
                if (original == tracked_it->second.originals.end() ||
                    !original->second.has_static_force ||
                    !std::isfinite(original->second.static_force)) {
                    baseline_complete = false;
                    break;
                }
                baseline_total += original->second.static_force;
                weighted_baseline_total +=
                    original->second.static_force * wheel.support_weight;
            }
            if (!baseline_complete || !std::isfinite(baseline_total) ||
                baseline_total <= 0.0 ||
                !std::isfinite(weighted_baseline_total) ||
                weighted_baseline_total <= 0.0) {
                LogOnce(LogLevel::Error, "static-force-baseline-invalid",
                        "Tracked original StaticForce support is incomplete; recovery was skipped with no writes");
                return false;
            }
            const double normalization =
                baseline_total / weighted_baseline_total;
            for (auto& wheel : *plan) {
                const auto& original =
                    tracked_it->second.originals.at(wheel.index);
                wheel.desired_static_force =
                    original.static_force * wheel.support_weight *
                    normalization;
                wheel.static_force_changed = !StaticForceEqual(
                    wheel.desired_static_force,
                    wheel.original_static_force);
            }
        }
        TrackedVehicle replacement;
        if (new_generation) {
            replacement.model_hash = vehicle.model_hash;
            replacement.wheel_generation = *generation_after;
            replacement.last_verified = now;
            for (const auto& wheel : *plan) {
                replacement.originals.emplace(
                    wheel.index,
                    OriginalWheelState{
                        wheel.original_flags,
                        wheel.original_steering_gain,
                        wheel.desired_steering_gain,
                        wheel.manages_steering_gain,
                        wheel.original_static_force,
                        wheel.manages_static_force,
                    });
            }
        }

        const auto fail_transaction =
            [&](std::size_t changed_through, const std::string& code,
                const std::string& detail) {
                if (state == RuntimeState::DisabledOnline) return false;
                const bool restored =
                    RollBack(vehicle, *plan, changed_through);
                if (state == RuntimeState::DisabledOnline) return false;
                bool baseline_retained = !new_generation;
                if (new_generation) {
                    if (restored) {
                        tracked.erase(vehicle.entity_id);
                    } else {
                        tracked[vehicle.entity_id] = std::move(replacement);
                        baseline_retained = true;
                    }
                }
                LogOnce(LogLevel::Error, code,
                        detail + " and rollback was " +
                            (restored ? "completed" : "incomplete") +
                            (baseline_retained
                                 ? "; original baseline retained for retry"
                                 : "; no modified state remains to restore"));
                return false;
            };

        bool static_force_written = false;
        for (std::size_t index = 0; index < plan->size(); ++index) {
            const auto& wheel = (*plan)[index];
            bool applied = true;
            if (wheel.flags_changed) {
                applied =
                    ConfirmOfflineBeforeWrite() &&
                    access.WriteWheelFlags(vehicle, wheel.index,
                                           wheel.desired_flags);
                if (applied) {
                    applied = ConfirmOfflineBeforeWrite() &&
                              access.SetWheelPowered(vehicle, wheel.index,
                                                     wheel.powered);
                }
            }
            if (applied && wheel.steering_gain_changed) {
                applied = ConfirmOfflineBeforeWrite() &&
                          access.WriteWheelSteeringGain(
                              vehicle, wheel.index,
                              wheel.desired_steering_gain);
            }
            if (applied && wheel.static_force_changed) {
                applied = ConfirmOfflineBeforeWrite() &&
                          access.WriteWheelStaticForce(
                              vehicle, wheel.index,
                              wheel.desired_static_force);
                static_force_written = static_force_written || applied;
            }
            if (!applied) {
                return fail_transaction(index + 1U, "wheel-write-failed",
                                        "Wheel update failed");
            }
        }

        if (static_force_written &&
            (!ConfirmOfflineBeforeWrite() ||
             !host.ActivatePhysics(vehicle))) {
            return fail_transaction(plan->size(),
                                    "physics-activation-failed",
                                    "Physics activation after StaticForce updates failed");
        }

        if (RequiresStaticForceAccess(configuration)) {
            double verified_total = 0.0;
            double expected_total = 0.0;
            for (const auto& wheel : *plan) {
                double verified = 0.0;
                if (!access.ReadWheelStaticForce(
                        vehicle, wheel.index, verified) ||
                    !std::isfinite(verified) ||
                    !StaticForceEqual(verified,
                                      wheel.desired_static_force)) {
                    return fail_transaction(
                        plan->size(), "static-force-verification-failed",
                        "StaticForce readback did not match the normalized support plan");
                }
                verified_total += verified;
                expected_total += wheel.desired_static_force;
            }
            if (!StaticForceEqual(verified_total, expected_total)) {
                return fail_transaction(
                    plan->size(), "static-force-total-changed",
                    "StaticForce verification detected a change in total vehicle support");
            }
        }

        if (new_generation) {
            tracked[vehicle.entity_id] = std::move(replacement);
            tracked_it = tracked.find(vehicle.entity_id);
        }
        tracked_it->second.last_verified = now;

        if (event_forced) {
            log.Write(LogLevel::Debug, "vehicle-event-applied",
                      "Applied " + std::string(reason) + " axle state to model " +
                          HashText(configuration.model_hash) + " with " +
                          std::to_string(configuration.axles.size()) + " axles");
        }
        return true;
    }

    bool ConfirmOfflineBeforeWrite() {
        if (!host.IsOnlineSession()) return true;
        DisableForOnline();
        return false;
    }

    void MaintainVolatileSteeringGains() {
        if (!access.SupportsSteeringGain()) return;
        for (const auto& [entity_id, saved] : tracked) {
            const auto current = host.LookupVehicle(entity_id);
            if (!current.has_value() || current->model_hash != saved.model_hash) {
                continue;
            }
            const auto current_generation = ResolveWheelGeneration(*current);
            if (!current_generation.has_value() ||
                *current_generation != saved.wheel_generation) {
                continue;
            }
            for (const auto& [index, original] : saved.originals) {
                if (!original.has_steering_gain) continue;
                double observed = 0.0;
                if (!access.ReadWheelSteeringGain(
                        *current, index, observed) ||
                    !std::isfinite(observed)) {
                    LogOnce(LogLevel::Warning,
                            "steering-gain-maintenance-read-failed",
                            "Could not read volatile steering gain for model " +
                                HashText(saved.model_hash) + " wheel " +
                                std::to_string(index));
                    continue;
                }
                if (std::abs(observed - original.desired_steering_gain) <=
                    kGainComparisonEpsilon) {
                    continue;
                }
                if (!ConfirmOfflineBeforeWrite()) return;
                if (!access.WriteWheelSteeringGain(
                        *current, index,
                        original.desired_steering_gain)) {
                    LogOnce(LogLevel::Warning,
                            "steering-gain-maintenance-write-failed",
                            "Could not reassert volatile steering gain for "
                            "model " + HashText(saved.model_hash) + " wheel " +
                                std::to_string(index));
                    continue;
                }
                double verified = 0.0;
                if (!access.ReadWheelSteeringGain(
                        *current, index, verified) ||
                    !std::isfinite(verified) ||
                    std::abs(verified - original.desired_steering_gain) >
                        kGainComparisonEpsilon) {
                    LogOnce(LogLevel::Warning,
                            "steering-gain-maintenance-verify-failed",
                            "Volatile steering gain did not survive immediate "
                            "readback for model " +
                                HashText(saved.model_hash) + " wheel " +
                                std::to_string(index));
                    continue;
                }
                LogOnce(LogLevel::Debug, "steering-gain-reasserted",
                        "Reasserted engine-owned steering gain for model " +
                            HashText(saved.model_hash) + " wheel " +
                            std::to_string(index));
            }
        }
    }

    void DisableForOnline() {
        if (state == RuntimeState::DisabledOnline) return;
        // Once an online session is observed, do not perform restoration writes.
        // Dropping host-owned identities is safer than touching online entities.
        tracked.clear();
        configurations.clear();
        access.Reset();
        state = RuntimeState::DisabledOnline;
        LogOnce(LogLevel::Error, "online-session-guard",
                "Online or network session detected; runtime disabled with no "
                "further reads or writes");
    }

    bool RestoreTracked() {
        if (!settings.restore_on_unload) {
            tracked.clear();
            return true;
        }
        if (host.IsOnlineSession()) {
            DisableForOnline();
            return true;
        }
        if (!access.IsResolved()) {
            LogOnce(LogLevel::Error, "restore-access-unavailable",
                    "Shutdown restoration is pending because the validated "
                    "wheel adapter is unavailable");
            return false;
        }
        for (auto iterator = tracked.begin(); iterator != tracked.end();) {
            const auto entity_id = iterator->first;
            const auto& saved = iterator->second;
            const auto current = host.LookupVehicle(entity_id);
            if (!current.has_value()) {
                // The host no longer owns this identity, so there is no live
                // storage left that can be restored safely. Retaining the
                // baseline would only fault every subsequent shutdown retry.
                LogOnce(
                    LogLevel::Info, "restore-entity-released",
                    "Model " + HashText(saved.model_hash) +
                        " no longer has a live tracked entity; its obsolete "
                        "restoration baseline was released without writes");
                iterator = tracked.erase(iterator);
                continue;
            }
            if (current->model_hash != saved.model_hash) {
                // Entity ids may be reused and wheel storage may be recreated.
                // Never apply an old baseline to the replacement identity.
                LogOnce(
                    LogLevel::Info, "restore-identity-replaced",
                    "Model " + HashText(saved.model_hash) +
                        " was replaced before restoration; its obsolete "
                        "baseline was released without touching the new entity");
                iterator = tracked.erase(iterator);
                continue;
            }
            const auto current_generation = ResolveWheelGeneration(*current);
            if (!current_generation.has_value()) {
                ++iterator;
                continue;
            }
            if (*current_generation != saved.wheel_generation) {
                // The entity/model pair survived, but its CWheel storage did
                // not. Never restore the old allocation's baseline into it.
                LogOnce(
                    LogLevel::Info, "restore-identity-replaced",
                    "Model " + HashText(saved.model_hash) +
                        " rebuilt its wheel storage before restoration; its "
                        "obsolete baseline was released without touching the "
                        "new wheel collection");
                iterator = tracked.erase(iterator);
                continue;
            }
            bool vehicle_restored = true;
            bool has_static_force_baseline = false;
            for (const auto& [index, original] : saved.originals) {
                std::uint16_t flags = 0;
                const bool flags_read =
                    access.ReadWheelFlags(*current, index, flags);
                if (!flags_read) {
                    vehicle_restored = false;
                } else {
                    const auto restored = static_cast<std::uint16_t>(
                        (flags & static_cast<std::uint16_t>(~kManagedWheelBits)) |
                        (original.flags & kManagedWheelBits));
                    if (restored != flags) {
                        if (!ConfirmOfflineBeforeWrite()) return true;
                        if (!access.WriteWheelFlags(*current, index, restored)) {
                            vehicle_restored = false;
                        }
                    }
                }
                // Power state may be backed by more than the exposed flag
                // word.  Reassert it on every retry, including when the flag
                // write succeeded during an earlier partial restore.
                if (!ConfirmOfflineBeforeWrite()) return true;
                if (!access.SetWheelPowered(
                        *current, index,
                        (original.flags & kDrivenBit) != 0)) {
                    vehicle_restored = false;
                }
                if (original.has_steering_gain) {
                    if (!access.SupportsSteeringGain()) {
                        vehicle_restored = false;
                    } else {
                        if (!ConfirmOfflineBeforeWrite()) return true;
                        if (!access.WriteWheelSteeringGain(
                                *current, index, original.steering_gain)) {
                            vehicle_restored = false;
                        }
                    }
                }
                if (original.has_static_force) {
                    has_static_force_baseline = true;
                    if (!access.SupportsStaticForce() ||
                        !host.SupportsPhysicsActivation()) {
                        vehicle_restored = false;
                    } else {
                        double current_force = 0.0;
                        if (!access.ReadWheelStaticForce(
                                *current, index, current_force) ||
                            !std::isfinite(current_force)) {
                            vehicle_restored = false;
                        } else if (!StaticForceEqual(
                                       current_force,
                                       original.static_force)) {
                            if (!ConfirmOfflineBeforeWrite()) return true;
                            if (!access.WriteWheelStaticForce(
                                    *current, index,
                                    original.static_force)) {
                                vehicle_restored = false;
                            }
                        }
                    }
                }
            }
            if (has_static_force_baseline) {
                if (!ConfirmOfflineBeforeWrite()) return true;
                if (!host.ActivatePhysics(*current)) {
                    vehicle_restored = false;
                }
            }
            if (has_static_force_baseline && access.SupportsStaticForce()) {
                for (const auto& [index, original] : saved.originals) {
                    if (!original.has_static_force) continue;
                    double verified = 0.0;
                    if (!access.ReadWheelStaticForce(
                            *current, index, verified) ||
                        !std::isfinite(verified) ||
                        !StaticForceEqual(verified,
                                          original.static_force)) {
                        vehicle_restored = false;
                    }
                }
            }
            if (vehicle_restored) {
                iterator = tracked.erase(iterator);
            } else {
                ++iterator;
            }
        }
        return tracked.empty();
    }
};

AxleRuntime::AxleRuntime(IVehicleHost& host, IWheelAccess& wheel_access,
                         ILogSink& log_sink, RuntimeSettings settings)
    : implementation_(std::make_unique<Implementation>(
          host, wheel_access, log_sink, std::move(settings))) {}

AxleRuntime::~AxleRuntime() { Shutdown(); }

bool AxleRuntime::Start(ConfigurationCatalog catalog,
                        ISignatureResolver& signature_resolver) {
    auto& runtime = *implementation_;
    if (runtime.state != RuntimeState::Stopped) {
        Shutdown();
        if (runtime.state != RuntimeState::Stopped) {
            runtime.LogOnce(
                LogLevel::Error, "restart-blocked-restore-pending",
                "Runtime restart was blocked because original wheel state is "
                "still pending restoration");
            return false;
        }
    }
    runtime.state = RuntimeState::Starting;
    runtime.emitted_once.clear();

    if (runtime.host.IsOnlineSession()) {
        runtime.DisableForOnline();
        return false;
    }
    for (const auto& issue : catalog.issues) {
        runtime.log.Write(issue.fatal ? LogLevel::Warning : LogLevel::Info,
                          issue.code,
                          issue.source_name.empty()
                              ? issue.message
                              : SafeSourceName(issue.source_name) + ": " +
                                    issue.message);
    }
    if (catalog.active.empty()) {
        runtime.state = RuntimeState::NoValidConfigurations;
        runtime.LogOnce(LogLevel::Warning, "no-valid-configurations",
                        "No non-conflicting axle configurations were loaded");
        return false;
    }

    const auto game = runtime.host.DetectGame();
    if (game.edition == Edition::Unknown ||
        runtime.access.TargetEdition() != game.edition ||
        !runtime.access.IsSupportedBuild(game) ||
        !runtime.access.Resolve(game, signature_resolver) ||
        !runtime.access.IsResolved()) {
        runtime.state = RuntimeState::UnsupportedBuild;
        runtime.LogOnce(LogLevel::Error, "unsupported-game-build",
                        std::string("Runtime ") + VWA_RUNTIME_VERSION + " detected " +
                            ToString(game.edition) + " build " +
                            std::to_string(game.build) + "; " +
                            runtime.access.LastFailure() +
                            ". No memory writes were attempted");
        return false;
    }

    for (auto& [model_hash, configuration] : catalog.active) {
        if (!runtime.EditionAllowed(configuration, game.edition)) {
            runtime.log.Write(LogLevel::Info, "configuration-target-disabled",
                              "Model " + HashText(model_hash) +
                                  " does not enable the detected edition");
            continue;
        }
        if (configuration.axles.size() >
            runtime.access.MaximumPhysicalAxles()) {
            runtime.log.Write(
                LogLevel::Warning, "target-axle-limit-exceeded",
                "Model " + HashText(model_hash) + " requires " +
                    std::to_string(configuration.axles.size()) +
                    " physical axles, exceeding the validated target limit; "
                    "additional wheels must remain cosmetic");
            continue;
        }
        if (RequiresSteeringGainAccess(configuration) &&
            !runtime.access.SupportsSteeringGain()) {
            runtime.log.Write(
                LogLevel::Warning, "steering-gain-capability-missing",
                "Model " + HashText(model_hash) +
                    " requests signed or scaled steering gain, but this exact "
                    "build profile validates only steering/drive flag access; "
                    "the configuration was disabled before vehicle writes");
            continue;
        }
        if (RequestsRuntimeGeometry(configuration) &&
            !runtime.access.SupportsWheelLocalPosition()) {
            runtime.log.Write(
                LogLevel::Warning,
                "wheel-local-position-capability-missing",
                "Model " + HashText(model_hash) +
                    " requests runtime steering geometry, but this exact "
                    "build profile cannot read authoritative vehicle-local "
                    "wheel positions; the configuration was disabled before "
                    "vehicle writes");
            continue;
        }
        if (RequiresStaticForceAccess(configuration) &&
            (!runtime.access.SupportsStaticForce() ||
             !runtime.host.SupportsPhysicsActivation())) {
            runtime.log.Write(
                LogLevel::Warning, "static-force-capability-missing",
                "Model " + HashText(model_hash) +
                    " requests axle support bias, but this exact build lacks "
                    "validated StaticForce access or physics activation; the "
                    "configuration was disabled before vehicle writes");
            continue;
        }
        if ((configuration.schema_version == kAxleSupportAxleSchemaVersion ||
             HasAnyStaticForceSetting(configuration)) &&
            !RequiresStaticForceAccess(configuration)) {
            runtime.log.Write(
                LogLevel::Warning, "incomplete-support-bias",
                "Model " + HashText(model_hash) +
                    " omits supportWeight on one or more physical axles; the "
                    "configuration was disabled before vehicle writes");
            continue;
        }
        runtime.log.Write(
            LogLevel::Info, "configuration-loaded",
            "Loaded model " + HashText(model_hash) + " with " +
                std::to_string(configuration.axles.size()) + " physical axles and " +
                std::to_string(configuration.expected_wheel_count) +
                " mapped wheel slots; steering polarity=" +
                configuration.steering_command_polarity);
        for (const auto& axle : configuration.axles) {
            runtime.log.Write(
                LogLevel::Debug, "steering-gain-resolved",
                "Model " + HashText(model_hash) + " axle " +
                    std::to_string(axle.order) + " base=" +
                    std::to_string(BaseSteeringGain(axle)) + " polarity=" +
                    configuration.steering_command_polarity + " effective=" +
                    std::to_string(
                        EffectiveSteeringGain(configuration, axle)));
        }
        runtime.configurations.emplace(model_hash, std::move(configuration));
    }
    if (runtime.configurations.empty()) {
        runtime.access.Reset();
        runtime.state = RuntimeState::NoValidConfigurations;
        runtime.LogOnce(LogLevel::Warning, "no-target-configurations",
                        "No valid configuration supports this target and its "
                        "validated physical axle limit");
        return false;
    }

    runtime.state = RuntimeState::Running;
    runtime.last_discovery = {};
    runtime.log.Write(LogLevel::Info, "runtime-started",
                      std::string("VehicleWorkbenchAxles ") +
                          VWA_RUNTIME_VERSION + " started for " +
                          ToString(game.edition) + " build " +
                          std::to_string(game.build) + " with " +
                          std::to_string(runtime.configurations.size()) +
                          " configuration(s)");
    return true;
}

void AxleRuntime::Service(std::chrono::steady_clock::time_point now) {
    auto& runtime = *implementation_;
    if (runtime.state != RuntimeState::Running) return;
    if (runtime.host.IsOnlineSession()) {
        runtime.DisableForOnline();
        return;
    }
    // GTA rebuilds its per-wheel steering-limit field during ordinary vehicle
    // simulation. Keep only explicitly steered, tracked gains current on every
    // host service tick; discovery and flag recovery remain rate-limited.
    runtime.MaintainVolatileSteeringGains();
    if (runtime.state != RuntimeState::Running) return;
    const auto discovery_interval =
        std::chrono::milliseconds(runtime.settings.discovery_interval_ms);
    if (runtime.last_discovery.time_since_epoch().count() != 0 &&
        now - runtime.last_discovery < discovery_interval) {
        return;
    }
    runtime.last_discovery = now;

    const auto vehicles = runtime.host.EnumerateVehicles();
    std::unordered_set<std::uint64_t> seen;
    const auto recovery_interval =
        std::chrono::milliseconds(runtime.settings.recovery_interval_ms);
    for (const auto& vehicle : vehicles) {
        const auto configured = runtime.configurations.find(vehicle.model_hash);
        if (configured == runtime.configurations.end()) continue;
        seen.insert(vehicle.entity_id);
        const auto live_generation =
            runtime.ResolveWheelGeneration(vehicle);
        if (!live_generation.has_value()) continue;
        const auto tracked = runtime.tracked.find(vehicle.entity_id);
        const bool new_or_recreated =
            tracked == runtime.tracked.end() ||
            tracked->second.model_hash != vehicle.model_hash ||
            tracked->second.wheel_generation != *live_generation;
        const bool recovery_due =
            tracked != runtime.tracked.end() &&
            now - tracked->second.last_verified >= recovery_interval;
        if (new_or_recreated || recovery_due) {
            runtime.Apply(vehicle, configured->second, now, false,
                          new_or_recreated ? "discovery" : "recovery");
        }
    }
    for (auto iterator = runtime.tracked.begin();
         iterator != runtime.tracked.end();) {
        if (seen.find(iterator->first) == seen.end()) {
            iterator = runtime.tracked.erase(iterator);
        } else {
            ++iterator;
        }
    }
}

void AxleRuntime::OnVehicleEvent(const VehicleSnapshot& vehicle,
                                 VehicleEvent event) {
    auto& runtime = *implementation_;
    if (runtime.state != RuntimeState::Running) return;
    if (runtime.host.IsOnlineSession()) {
        runtime.DisableForOnline();
        return;
    }
    const auto configured = runtime.configurations.find(vehicle.model_hash);
    if (configured == runtime.configurations.end()) return;
    runtime.Apply(vehicle, configured->second,
                  std::chrono::steady_clock::now(), true, EventText(event));
}

void AxleRuntime::Shutdown() {
    if (!implementation_) return;
    auto& runtime = *implementation_;
    if (runtime.state == RuntimeState::Stopped) return;
    if (runtime.state == RuntimeState::Running ||
        runtime.state == RuntimeState::Faulted) {
        if (!runtime.RestoreTracked()) {
            runtime.configurations.clear();
            runtime.state = RuntimeState::Faulted;
            runtime.LogOnce(
                LogLevel::Error, "shutdown-restore-pending",
                "Runtime stopped new work, but original wheel state could not "
                "be fully restored; call Shutdown again to retry safely");
            return;
        }
    } else {
        runtime.tracked.clear();
    }
    runtime.configurations.clear();
    runtime.access.Reset();
    runtime.state = RuntimeState::Stopped;
    runtime.log.Write(LogLevel::Info, "runtime-stopped",
                      "Runtime stopped; tracked entity state was released");
}

RuntimeState AxleRuntime::State() const noexcept {
    return implementation_->state;
}

std::size_t AxleRuntime::TrackedVehicleCount() const noexcept {
    return implementation_->tracked.size();
}

std::size_t AxleRuntime::ActiveConfigurationCount() const noexcept {
    return implementation_->configurations.size();
}

} // namespace vwa
