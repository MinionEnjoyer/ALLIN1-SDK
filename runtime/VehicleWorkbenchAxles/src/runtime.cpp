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
#define VWA_RUNTIME_VERSION "2.1.0"
#endif

namespace vwa {

namespace {

constexpr std::uint16_t kManagedWheelBits =
    static_cast<std::uint16_t>(kSteeredBit | kDrivenBit);
constexpr double kGainComparisonEpsilon = kSteeringGainEpsilon;

double EffectiveSteeringGain(const AxleDefinition& axle) noexcept {
    return axle.steering_gain.value_or(axle.steered ? 1.0 : 0.0);
}

bool RequiresSteeringGainAccess(
    const AxleConfiguration& configuration) noexcept {
    return std::any_of(
        configuration.axles.begin(), configuration.axles.end(),
        [](const AxleDefinition& axle) {
            const double legacy_gain = axle.steered ? 1.0 : 0.0;
            return std::abs(EffectiveSteeringGain(axle) - legacy_gain) >
                   kGainComparisonEpsilon;
        });
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
        bool has_steering_gain{false};
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
        bool flags_changed{false};
        bool steering_gain_changed{false};
        bool manages_steering_gain{false};
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

    bool EditionAllowed(const AxleConfiguration& configuration,
                        Edition edition) const noexcept {
        return (edition == Edition::Legacy && configuration.story_legacy) ||
               (edition == Edition::Enhanced && configuration.story_enhanced);
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

        std::vector<WheelPlan> plan;
        plan.reserve(configuration.axles.size() * 2U);
        const bool manage_steering_gain =
            RequiresSteeringGainAccess(configuration);
        if (manage_steering_gain && !access.SupportsSteeringGain()) {
            LogOnce(LogLevel::Error, "steering-gain-capability-missing",
                    "Model " + HashText(configuration.model_hash) +
                        " requests signed or scaled steering gain, but the "
                        "validated build profile exposes steering flags only; "
                        "no changes were applied");
            return std::nullopt;
        }
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
                wheel.desired_steering_gain = EffectiveSteeringGain(axle);
                wheel.manages_steering_gain = manage_steering_gain;
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
        for (auto& wheel : plan) {
            if (!access.ReadWheelFlags(vehicle, wheel.index,
                                       wheel.original_flags)) {
                LogOnce(LogLevel::Error, "wheel-read-failed",
                        "Wheel flags could not be read safely; model " +
                            HashText(configuration.model_hash) + " was skipped");
                return std::nullopt;
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
        return plan;
    }

    bool RollBack(const VehicleSnapshot& vehicle,
                  const std::vector<WheelPlan>& plan,
                  std::size_t changed_through) {
        bool complete = true;
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

        auto plan = BuildPlan(vehicle, configuration, game_wheel_count);
        if (!plan.has_value()) return false;

        // Reads and plan construction can take an arbitrary amount of time.
        // Re-check the session at the write boundary, then again before each
        // individual mutating adapter call below.
        if (!ConfirmOfflineBeforeWrite()) return false;

        auto tracked_it = tracked.find(vehicle.entity_id);
        const bool new_generation =
            tracked_it == tracked.end() ||
            tracked_it->second.wheel_generation != vehicle.wheel_generation ||
            tracked_it->second.model_hash != vehicle.model_hash;
        TrackedVehicle replacement;
        if (new_generation) {
            replacement.model_hash = vehicle.model_hash;
            replacement.wheel_generation = vehicle.wheel_generation;
            replacement.last_verified = now;
            for (const auto& wheel : *plan) {
                replacement.originals.emplace(
                    wheel.index,
                    OriginalWheelState{
                        wheel.original_flags,
                        wheel.original_steering_gain,
                        wheel.manages_steering_gain,
                    });
            }
        }

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
            if (!applied) {
                if (state == RuntimeState::DisabledOnline) return false;
                const bool restored = RollBack(vehicle, *plan, index + 1U);
                if (state == RuntimeState::DisabledOnline) return false;
                bool baseline_retained = !new_generation;
                if (new_generation) {
                    if (restored) {
                        tracked.erase(vehicle.entity_id);
                    } else {
                        // Rollback itself was incomplete.  Retain the original
                        // state captured before the first write so shutdown or
                        // a later lifecycle pass can safely retry restoration.
                        tracked[vehicle.entity_id] = std::move(replacement);
                        baseline_retained = true;
                    }
                }
                LogOnce(LogLevel::Error, "wheel-write-failed",
                        std::string("Wheel update failed and rollback was ") +
                            (restored ? "completed" : "incomplete") +
                            (baseline_retained
                                 ? "; original baseline retained for retry"
                                 : "; no modified state remains to restore"));
                return false;
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
            if (current->model_hash != saved.model_hash ||
                current->wheel_generation != saved.wheel_generation) {
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
            bool vehicle_restored = true;
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
        runtime.log.Write(
            LogLevel::Info, "configuration-loaded",
            "Loaded model " + HashText(model_hash) + " with " +
                std::to_string(configuration.axles.size()) + " physical axles and " +
                std::to_string(configuration.expected_wheel_count) +
                " mapped wheel slots");
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
        const auto tracked = runtime.tracked.find(vehicle.entity_id);
        const bool new_or_recreated =
            tracked == runtime.tracked.end() ||
            tracked->second.model_hash != vehicle.model_hash ||
            tracked->second.wheel_generation != vehicle.wheel_generation;
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
