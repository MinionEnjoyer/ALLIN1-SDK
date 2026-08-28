#include "vehicle_workbench_axles/configuration.hpp"
#include "vehicle_workbench_axles/runtime.hpp"
#include "vehicle_workbench_axles/runtime_settings_document.hpp"
#include "vehicle_workbench_axles/wheel_access.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vwa;

void Check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

class LogSink final : public ILogSink {
public:
    void Write(LogLevel, const std::string& code,
               const std::string& message) override {
        entries.emplace_back(code, message);
    }
    std::vector<std::pair<std::string, std::string>> entries;
};

class Resolver final : public ISignatureResolver {
public:
    std::optional<std::uintptr_t>
    Resolve(const std::string&, const std::string&) override {
        return allow ? std::optional<std::uintptr_t>(0x1000U) : std::nullopt;
    }
    bool IsExecutable(std::uintptr_t, std::size_t) const override {
        return allow;
    }
    bool IsInGameModule(std::uintptr_t, std::size_t) const override {
        return allow;
    }
    bool BytesMatch(std::uintptr_t,
                    const std::vector<std::uint8_t>&) const override {
        return allow;
    }
    bool allow{false};
};

class CompiledProfile final : public ICompiledWheelProfile {
public:
    GameIdentity Identity() const override {
        return {Edition::Legacy, 9999, "fixture"};
    }
    std::uint32_t MaximumPhysicalAxles() const noexcept override { return 5; }
    std::vector<SignatureRequirement> SignatureRequirements() const override {
        return {{"fixture-accessor", "AA BB", "xx", {0xAA, 0xBB}, 2}};
    }
    bool Bind(const std::vector<std::uintptr_t>& addresses,
              std::string&) override {
        bound = addresses.size() == 1 && addresses.front() == 0x1000U;
        return bound;
    }
    void Unbind() noexcept override { bound = false; }
    bool GetWheelCount(const VehicleSnapshot&, std::uint32_t& count) override {
        if (!bound) return false;
        count = static_cast<std::uint32_t>(flags.size());
        return true;
    }
    bool ReadWheelFlags(const VehicleSnapshot&, std::uint32_t index,
                        std::uint16_t& value) override {
        if (!bound || index >= flags.size()) return false;
        value = flags[index];
        return true;
    }
    bool WriteWheelFlags(const VehicleSnapshot&, std::uint32_t index,
                         std::uint16_t value) override {
        if (!bound || index >= flags.size()) return false;
        flags[index] = value;
        return true;
    }
    bool SetWheelPowered(const VehicleSnapshot&, std::uint32_t index,
                         bool powered) override {
        if (!bound || index >= flags.size()) return false;
        flags[index] = powered
                           ? static_cast<std::uint16_t>(flags[index] | kDrivenBit)
                           : static_cast<std::uint16_t>(flags[index] & ~kDrivenBit);
        return true;
    }
    bool SupportsWheelGenerationToken() const noexcept override {
        return true;
    }
    bool ReadWheelGenerationToken(const VehicleSnapshot& vehicle,
                                  std::uint64_t& generation) override {
        if (!bound) return false;
        generation = generation_token ^ vehicle.entity_id;
        return generation != 0;
    }

    bool bound{false};
    std::vector<std::uint16_t> flags{0, 0, 0, 0};
    std::uint64_t generation_token{0xA11E0000ULL};
};

class Host final : public IVehicleHost {
public:
    GameIdentity DetectGame() const override { return game; }
    bool IsOnlineSession() const override {
        ++online_checks;
        return online ||
               (online_on_check.has_value() &&
                online_checks >= *online_on_check);
    }
    std::vector<VehicleSnapshot> EnumerateVehicles() override {
        return vehicles;
    }
    std::optional<VehicleSnapshot>
    LookupVehicle(std::uint64_t entity_id) override {
        for (const auto& vehicle : vehicles) {
            if (vehicle.entity_id == entity_id) return vehicle;
        }
        return std::nullopt;
    }
    bool SupportsPhysicsActivation() const noexcept override {
        return supports_physics_activation;
    }
    bool ActivatePhysics(const VehicleSnapshot&) override {
        ++physics_activation_calls;
        return physics_activation_succeeds;
    }

    GameIdentity game{Edition::Legacy, 9999, "mock-fingerprint"};
    bool online{false};
    std::optional<std::size_t> online_on_check;
    mutable std::size_t online_checks{0};
    bool supports_physics_activation{false};
    bool physics_activation_succeeds{true};
    std::size_t physics_activation_calls{0};
    std::vector<VehicleSnapshot> vehicles;
};

class WheelAccess final : public IWheelAccess {
public:
    bool Resolve(const GameIdentity& game, ISignatureResolver&) override {
        ++resolve_calls;
        resolved = supported && game.edition == target;
        if (!resolved) failure = "mock unsupported build";
        return resolved;
    }
    bool IsSupportedBuild(const GameIdentity& game) const override {
        return supported && game.edition == target;
    }
    bool IsResolved() const noexcept override { return resolved; }
    Edition TargetEdition() const noexcept override { return target; }
    std::uint32_t MaximumPhysicalAxles() const noexcept override {
        return maximum_axles;
    }
    const std::string& LastFailure() const noexcept override { return failure; }
    void Reset() noexcept override { resolved = false; }
    bool GetWheelCount(const VehicleSnapshot&, std::uint32_t& count) override {
        if (!resolved) return false;
        count = static_cast<std::uint32_t>(flags.size());
        return true;
    }
    bool ReadWheelFlags(const VehicleSnapshot&, std::uint32_t index,
                        std::uint16_t& value) override {
        if (!resolved || index >= flags.size()) return false;
        if (fail_next_read_at.has_value() && *fail_next_read_at == index) {
            fail_next_read_at.reset();
            return false;
        }
        value = flags[index];
        ++reads;
        return true;
    }
    bool WriteWheelFlags(const VehicleSnapshot&, std::uint32_t index,
                         std::uint16_t value) override {
        if (!resolved || index >= flags.size()) return false;
        if (fail_next_write_at.has_value() &&
            *fail_next_write_at == index) {
            fail_next_write_at.reset();
            return false;
        }
        flags[index] = value;
        ++writes;
        return true;
    }
    bool SetWheelPowered(const VehicleSnapshot&, std::uint32_t index,
                         bool powered) override {
        if (!resolved || index >= flags.size()) return false;
        if (fail_next_power_write_at.has_value() &&
            *fail_next_power_write_at == index) {
            fail_next_power_write_at.reset();
            return false;
        }
        if (powered) {
            flags[index] = static_cast<std::uint16_t>(flags[index] | kDrivenBit);
        } else {
            flags[index] =
                static_cast<std::uint16_t>(flags[index] & ~kDrivenBit);
        }
        ++powered_writes;
        return true;
    }
    bool SupportsWheelBoneId() const noexcept override {
        return supports_wheel_bone_id;
    }
    bool ReadWheelBoneId(const VehicleSnapshot&, std::uint32_t index,
                         std::int32_t& bone_id) override {
        if (!resolved || !supports_wheel_bone_id || index >= bone_ids.size()) {
            return false;
        }
        if (fail_next_bone_read_at.has_value() &&
            *fail_next_bone_read_at == index) {
            fail_next_bone_read_at.reset();
            return false;
        }
        bone_id = bone_ids[index];
        ++bone_id_reads;
        return true;
    }
    bool SupportsWheelGenerationToken() const noexcept override {
        return supports_wheel_generation_token;
    }
    bool ReadWheelGenerationToken(const VehicleSnapshot&, std::uint64_t& token)
        override {
        if (!resolved || !supports_wheel_generation_token) return false;
        if (fail_next_generation_read) {
            fail_next_generation_read = false;
            return false;
        }
        if (change_generation_read_number.has_value() &&
            *change_generation_read_number == generation_reads) {
            ++generation_token;
            change_generation_read_number.reset();
        }
        token = generation_token;
        ++generation_reads;
        return token != 0;
    }
    bool SupportsSteeringGain() const noexcept override {
        return supports_steering_gain;
    }
    bool ReadWheelSteeringGain(const VehicleSnapshot&, std::uint32_t index,
                               double& gain) override {
        if (!resolved || !supports_steering_gain ||
            index >= steering_gains.size()) {
            return false;
        }
        gain = steering_gains[index];
        ++gain_reads;
        return true;
    }
    bool WriteWheelSteeringGain(const VehicleSnapshot&, std::uint32_t index,
                                double gain) override {
        if (!resolved || !supports_steering_gain ||
            index >= steering_gains.size()) {
            return false;
        }
        if (fail_next_gain_write_at.has_value() &&
            *fail_next_gain_write_at == index) {
            fail_next_gain_write_at.reset();
            return false;
        }
        steering_gains[index] = gain;
        ++gain_writes;
        return true;
    }
    bool SupportsWheelLocalPosition() const noexcept override {
        return supports_wheel_local_position;
    }
    bool ReadWheelLocalPosition(const VehicleSnapshot&, std::uint32_t index,
                                WheelLocalPosition& position) override {
        if (!resolved || !supports_wheel_local_position ||
            index >= wheel_local_positions.size()) {
            return false;
        }
        if (fail_next_position_read_at.has_value() &&
            *fail_next_position_read_at == index) {
            fail_next_position_read_at.reset();
            return false;
        }
        position = wheel_local_positions[index];
        ++position_reads;
        return true;
    }
    bool SupportsStaticForce() const noexcept override {
        return supports_static_force;
    }
    bool ReadWheelStaticForce(const VehicleSnapshot&, std::uint32_t index,
                              double& force) override {
        if (!resolved || !supports_static_force ||
            index >= static_forces.size()) {
            return false;
        }
        force = static_forces[index];
        if ((corrupt_next_static_read_at.has_value() &&
             *corrupt_next_static_read_at == index) ||
            (corrupt_static_read_number.has_value() &&
             *corrupt_static_read_number == static_force_reads)) {
            corrupt_next_static_read_at.reset();
            corrupt_static_read_number.reset();
            force += 1.0;
        }
        ++static_force_reads;
        return true;
    }
    bool WriteWheelStaticForce(const VehicleSnapshot&, std::uint32_t index,
                               double force) override {
        if (!resolved || !supports_static_force ||
            index >= static_forces.size()) {
            return false;
        }
        if (fail_next_static_write_at.has_value() &&
            *fail_next_static_write_at == index) {
            fail_next_static_write_at.reset();
            return false;
        }
        static_forces[index] = force;
        ++static_force_writes;
        return true;
    }

    Edition target{Edition::Legacy};
    bool supported{true};
    bool resolved{false};
    std::uint32_t maximum_axles{5};
    std::string failure;
    std::vector<std::uint16_t> flags;
    std::vector<std::int32_t> bone_ids;
    std::vector<double> steering_gains;
    std::vector<WheelLocalPosition> wheel_local_positions;
    std::vector<double> static_forces;
    bool supports_steering_gain{false};
    bool supports_wheel_bone_id{false};
    bool supports_wheel_generation_token{false};
    bool supports_wheel_local_position{false};
    bool supports_static_force{false};
    std::optional<std::uint32_t> fail_next_read_at;
    std::optional<std::uint32_t> fail_next_write_at;
    std::optional<std::uint32_t> fail_next_power_write_at;
    std::optional<std::uint32_t> fail_next_gain_write_at;
    std::optional<std::uint32_t> fail_next_bone_read_at;
    std::optional<std::uint32_t> fail_next_position_read_at;
    std::optional<std::uint32_t> fail_next_static_write_at;
    std::optional<std::uint32_t> corrupt_next_static_read_at;
    std::optional<std::size_t> corrupt_static_read_number;
    std::optional<std::size_t> change_generation_read_number;
    bool fail_next_generation_read{false};
    std::size_t reads{0};
    std::size_t writes{0};
    std::size_t powered_writes{0};
    std::size_t bone_id_reads{0};
    std::size_t generation_reads{0};
    std::size_t gain_reads{0};
    std::size_t gain_writes{0};
    std::size_t position_reads{0};
    std::size_t static_force_reads{0};
    std::size_t static_force_writes{0};
    std::size_t resolve_calls{0};
    std::uint64_t generation_token{1};
};

const std::vector<std::pair<std::string, std::string>> kAllPairs{
    {"wheel_lf", "wheel_rf"},
    {"wheel_lm1", "wheel_rm1"},
    {"wheel_lm2", "wheel_rm2"},
    {"wheel_lm3", "wheel_rm3"},
    {"wheel_lr", "wheel_rr"},
};

const std::map<std::string, std::int32_t> kCanonicalWheelBoneIds{
    {"wheel_lf", 11},  {"wheel_rf", 12},  {"wheel_lr", 13},
    {"wheel_rr", 14},  {"wheel_lm1", 15}, {"wheel_rm1", 16},
    {"wheel_lm2", 17}, {"wheel_rm2", 18}, {"wheel_lm3", 19},
    {"wheel_rm3", 20},
};

AxleConfiguration MakeConfiguration(std::size_t axle_count,
                                    std::uint32_t model_hash = 0x12345678U) {
    AxleConfiguration result;
    result.configuration_id = "fixture-" + std::to_string(axle_count);
    result.model_name = "fixture";
    result.model_hash = model_hash;
    result.expected_wheel_count = static_cast<std::uint32_t>(axle_count * 2U);
    result.minimum_runtime_version = "1.0.0";
    result.story_legacy = true;
    result.story_enhanced = true;

    std::vector<std::pair<std::string, std::string>> selected;
    selected.push_back(kAllPairs.front());
    for (std::size_t index = 1; index + 1 < axle_count; ++index) {
        selected.push_back(kAllPairs[index]);
    }
    selected.push_back(kAllPairs.back());

    // Reverse the index order deliberately: runtime correctness must come from
    // canonical bone semantics and this exported map, never order * 2.
    std::uint32_t next_index = result.expected_wheel_count;
    for (std::size_t position = 0; position < selected.size(); ++position) {
        const auto& pair = selected[position];
        AxleDefinition axle;
        axle.order = static_cast<std::uint32_t>(position);
        axle.role = position == 0
                        ? "front"
                        : (position + 1 == selected.size() ? "rear" : "middle");
        axle.left_bone = pair.first;
        axle.right_bone = pair.second;
        axle.steered = (position % 2U) == 0;
        axle.powered = position > 0;
        result.axles.push_back(axle);
        result.wheel_index_map.emplace(pair.first, --next_index);
        result.wheel_index_map.emplace(pair.second, --next_index);
    }
    return result;
}

void PromoteToSignedSchema(AxleConfiguration& configuration) {
    configuration.schema_version = kSignedSteeringAxleSchemaVersion;
    configuration.minimum_runtime_version = kSignedSteeringMinimumRuntime;
    SteeringCalculationEvidence evidence;
    evidence.mode = "manual";
    evidence.algorithm_version = 1;
    evidence.bone_position_sha256 = std::string(64U, '0');
    configuration.steering_calculation = std::move(evidence);
    for (auto& axle : configuration.axles) {
        axle.steering_gain = axle.steered ? 1.0 : 0.0;
    }
}

std::vector<std::uint16_t> InitialFlags(std::size_t count) {
    std::vector<std::uint16_t> result;
    for (std::size_t index = 0; index < count; ++index) {
        const auto managed = index % 2U == 0 ? kDrivenBit : kSteeredBit;
        result.push_back(static_cast<std::uint16_t>(0xA400U | managed));
    }
    return result;
}

void VerifyDesiredFlags(const AxleConfiguration& configuration,
                        const std::vector<std::uint16_t>& before,
                        const std::vector<std::uint16_t>& after) {
    Check(before.size() == after.size(), "wheel vector size changed");
    std::map<std::uint32_t, std::pair<bool, bool>> desired;
    for (const auto& axle : configuration.axles) {
        desired.emplace(configuration.wheel_index_map.at(axle.left_bone),
                        std::make_pair(axle.steered, axle.powered));
        desired.emplace(configuration.wheel_index_map.at(axle.right_bone),
                        std::make_pair(axle.steered, axle.powered));
    }
    for (std::size_t index = 0; index < before.size(); ++index) {
        Check((before[index] & static_cast<std::uint16_t>(~0x18U)) ==
                  (after[index] & static_cast<std::uint16_t>(~0x18U)),
              "unrelated wheel bits were modified");
        Check(((after[index] & kSteeredBit) != 0) == desired.at(index).first,
              "steering bit does not match mapped axle");
        Check(((after[index] & kDrivenBit) != 0) == desired.at(index).second,
              "driven bit does not match mapped axle");
    }
}

void TestVariableLengthRuntime() {
    for (std::size_t axle_count = 2; axle_count <= 5; ++axle_count) {
        Host host;
        WheelAccess access;
        LogSink log;
        Resolver resolver;
        auto configuration = MakeConfiguration(axle_count);
        const auto issues = ValidateConfiguration(configuration, "1.0.0");
        Check(issues.empty(), "valid variable axle configuration was rejected");
        const auto before = InitialFlags(configuration.expected_wheel_count);
        access.flags = before;
        host.vehicles = {{1, configuration.model_hash, 10}};
        ConfigurationCatalog catalog;
        catalog.active.emplace(configuration.model_hash, configuration);

        AxleRuntime runtime(host, access, log);
        Check(runtime.Start(std::move(catalog), resolver),
              "mock runtime did not start");
        const auto first = std::chrono::steady_clock::now();
        runtime.Service(first);
        VerifyDesiredFlags(configuration, before, access.flags);
        Check(runtime.TrackedVehicleCount() == 1,
              "configured vehicle was not tracked");

        access.flags = before;
        runtime.OnVehicleEvent(host.vehicles.front(), VehicleEvent::Repaired);
        VerifyDesiredFlags(configuration, before, access.flags);

        access.flags = before;
        host.vehicles.front().wheel_generation = 11;
        runtime.OnVehicleEvent(host.vehicles.front(),
                               VehicleEvent::WheelStateRecreated);
        VerifyDesiredFlags(configuration, before, access.flags);

        access.flags = before;
        runtime.Service(first + std::chrono::seconds(3));
        VerifyDesiredFlags(configuration, before, access.flags);

        // Unload restoration changes only the managed bits; simulate an
        // unrelated game bit changing after application.
        access.flags[0] = static_cast<std::uint16_t>(access.flags[0] ^ 0x0200U);
        const auto unknown_before_shutdown =
            static_cast<std::uint16_t>(access.flags[0] & ~0x18U);
        runtime.Shutdown();
        Check((access.flags[0] & static_cast<std::uint16_t>(~0x18U)) ==
                  unknown_before_shutdown,
              "shutdown restoration overwrote an unrelated bit");
        Check((access.flags[0] & 0x18U) == (before[0] & 0x18U),
              "shutdown did not restore managed bits");
    }
}

void ConfigureLiveBoneIds(const AxleConfiguration& configuration,
                          WheelAccess& access) {
    access.supports_wheel_bone_id = true;
    access.bone_ids.assign(configuration.expected_wheel_count, -1);
    for (const auto& [bone, index] : configuration.wheel_index_map) {
        access.bone_ids.at(index) = kCanonicalWheelBoneIds.at(bone);
    }
}

void PromoteToSupportBias(AxleConfiguration& configuration,
                          const std::vector<double>& weights = {}) {
    configuration.schema_version = kAxleSupportAxleSchemaVersion;
    configuration.minimum_runtime_version = kAxleSupportMinimumRuntime;
    configuration.steering_calculation.reset();
    for (std::size_t index = 0; index < configuration.axles.size(); ++index) {
        auto& axle = configuration.axles[index];
        axle.steering_gain = axle.steered ? 1.0 : 0.0;
        axle.suspension = AxleSuspension{
            weights.empty() ? 1.0 : weights.at(index)};
    }
}

void TestLiveBoneFingerprintBlocksWrongOverride() {
    auto configuration = MakeConfiguration(3);

    Host host;
    WheelAccess access;
    LogSink log;
    Resolver resolver;
    access.flags = InitialFlags(configuration.expected_wheel_count);
    ConfigureLiveBoneIds(configuration, access);
    // Simulate a stale or incorrectly authored physical-order override.
    std::swap(access.bone_ids[0], access.bone_ids[1]);
    host.vehicles = {{77, configuration.model_hash, 1}};
    ConfigurationCatalog catalog;
    catalog.active.emplace(configuration.model_hash, configuration);

    AxleRuntime runtime(host, access, log);
    Check(runtime.Start(std::move(catalog), resolver),
          "bone-fingerprint fixture failed to start");
    runtime.Service(std::chrono::steady_clock::now());
    Check(access.writes == 0 && access.powered_writes == 0,
          "mismatched live wheel bones allowed axle writes");
    Check(std::any_of(log.entries.begin(), log.entries.end(),
                      [](const auto& entry) {
                          return entry.first == "runtime-wheel-bone-mismatch";
                      }),
          "mismatched live wheel bones did not emit a diagnostic");
    runtime.Shutdown();
}

void TestLiveBoneFingerprintAcceptsCanonicalMapAndFailsClosedOnReadError() {
    Resolver resolver;
    auto configuration = MakeConfiguration(3, 0xB0A1D001U);

    Host valid_host;
    WheelAccess valid_access;
    LogSink valid_log;
    const auto valid_before = InitialFlags(configuration.expected_wheel_count);
    valid_access.flags = valid_before;
    ConfigureLiveBoneIds(configuration, valid_access);
    valid_host.vehicles = {{78, configuration.model_hash, 1}};
    ConfigurationCatalog valid_catalog;
    valid_catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime valid_runtime(valid_host, valid_access, valid_log);
    Check(valid_runtime.Start(std::move(valid_catalog), resolver),
          "canonical live-bone fixture failed to start");
    valid_runtime.Service(std::chrono::steady_clock::now());
    VerifyDesiredFlags(configuration, valid_before, valid_access.flags);
    Check(valid_access.bone_id_reads == configuration.expected_wheel_count,
          "canonical live wheel mapping was not verified exactly once");
    Check(valid_runtime.TrackedVehicleCount() == 1,
          "canonical live wheel mapping was not tracked");
    valid_runtime.Shutdown();

    Host failed_host;
    WheelAccess failed_access;
    LogSink failed_log;
    const auto failed_before = InitialFlags(configuration.expected_wheel_count);
    failed_access.flags = failed_before;
    ConfigureLiveBoneIds(configuration, failed_access);
    const auto failed_slot =
        configuration.wheel_index_map.at(configuration.axles.front().left_bone);
    failed_access.fail_next_bone_read_at = failed_slot;
    failed_host.vehicles = {{79, configuration.model_hash, 1}};
    ConfigurationCatalog failed_catalog;
    failed_catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime failed_runtime(failed_host, failed_access, failed_log);
    Check(failed_runtime.Start(std::move(failed_catalog), resolver),
          "live-bone read-failure fixture failed to start");
    failed_runtime.Service(std::chrono::steady_clock::now());
    Check(failed_access.flags == failed_before && failed_access.writes == 0 &&
              failed_access.powered_writes == 0,
          "failed live wheel-bone read allowed axle writes");
    Check(std::any_of(
              failed_log.entries.begin(), failed_log.entries.end(),
              [](const auto& entry) {
                  return entry.first == "runtime-wheel-bone-mismatch";
              }),
          "failed live wheel-bone read did not produce a fail-closed diagnostic");
    failed_runtime.Shutdown();
}

void TestLiveWheelGenerationRebuildsBaseline() {
    Host host;
    WheelAccess access;
    LogSink log;
    Resolver resolver;
    auto configuration = MakeConfiguration(3, 0x6E4E0001U);
    const auto first_baseline = InitialFlags(configuration.expected_wheel_count);
    access.flags = first_baseline;
    access.supports_wheel_generation_token = true;
    access.generation_token = 101;
    // The host identity deliberately never changes. Only the validated wheel
    // adapter can observe that GTA replaced the underlying CWheel storage.
    host.vehicles = {{301, configuration.model_hash, 44}};
    ConfigurationCatalog catalog;
    catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime runtime(host, access, log);
    Check(runtime.Start(std::move(catalog), resolver),
          "live-generation fixture failed to start");
    const auto first = std::chrono::steady_clock::now();
    runtime.Service(first);
    Check(runtime.TrackedVehicleCount() == 1,
          "first wheel generation was not tracked");

    auto replacement_baseline = InitialFlags(configuration.expected_wheel_count);
    for (std::size_t index = 0; index < replacement_baseline.size(); ++index) {
        replacement_baseline[index] = static_cast<std::uint16_t>(
            (0xB200U + static_cast<std::uint16_t>(index << 5U)) |
            (index % 3U == 0U ? kSteeredBit : kDrivenBit));
    }
    access.flags = replacement_baseline;
    ++access.generation_token;
    runtime.Service(first + std::chrono::seconds(1));
    VerifyDesiredFlags(configuration, replacement_baseline, access.flags);
    Check(runtime.TrackedVehicleCount() == 1,
          "replacement wheel generation was not tracked");

    runtime.Shutdown();
    Check(runtime.State() == RuntimeState::Stopped &&
              access.flags == replacement_baseline,
          "shutdown restored the stale wheel generation instead of the replacement baseline");
}

void TestLiveWheelGenerationGuardsPlanningAndRestoration() {
    Resolver resolver;

    {
        Host host;
        WheelAccess access;
        LogSink log;
        auto configuration = MakeConfiguration(3, 0x6E4E0002U);
        const auto before = InitialFlags(configuration.expected_wheel_count);
        access.flags = before;
        access.supports_wheel_generation_token = true;
        access.change_generation_read_number = 1;
        host.vehicles = {{302, configuration.model_hash, 55}};
        ConfigurationCatalog catalog;
        catalog.active.emplace(configuration.model_hash, configuration);
        AxleRuntime runtime(host, access, log);
        Check(runtime.Start(std::move(catalog), resolver),
              "stale-plan generation fixture failed to start");
        runtime.OnVehicleEvent(host.vehicles.front(), VehicleEvent::Repaired);
        Check(access.flags == before && access.writes == 0 &&
                  access.powered_writes == 0 &&
                  runtime.TrackedVehicleCount() == 0,
              "wheel generation change during planning allowed stale writes");
        Check(std::any_of(
                  log.entries.begin(), log.entries.end(),
                  [](const auto& entry) {
                      return entry.first ==
                             "wheel-generation-changed-during-plan";
                  }),
              "stale wheel plan rejection was not diagnosed");
        runtime.Shutdown();
    }

    {
        Host host;
        WheelAccess access;
        LogSink log;
        auto configuration = MakeConfiguration(3, 0x6E4E0003U);
        access.flags = InitialFlags(configuration.expected_wheel_count);
        access.supports_wheel_generation_token = true;
        host.vehicles = {{303, configuration.model_hash, 66}};
        ConfigurationCatalog catalog;
        catalog.active.emplace(configuration.model_hash, configuration);
        AxleRuntime runtime(host, access, log);
        Check(runtime.Start(std::move(catalog), resolver),
              "replacement-before-restore fixture failed to start");
        runtime.Service(std::chrono::steady_clock::now());

        auto replacement = InitialFlags(configuration.expected_wheel_count);
        for (std::size_t index = 0; index < replacement.size(); ++index) {
            replacement[index] = static_cast<std::uint16_t>(
                0xC000U | (index % 2U == 0U ? kSteeredBit : kDrivenBit));
        }
        access.flags = replacement;
        ++access.generation_token;
        const auto writes_before = access.writes;
        const auto powered_writes_before = access.powered_writes;
        runtime.Shutdown();
        Check(runtime.State() == RuntimeState::Stopped &&
                  runtime.TrackedVehicleCount() == 0 &&
                  access.flags == replacement,
              "shutdown touched replacement CWheel storage");
        Check(access.writes == writes_before &&
                  access.powered_writes == powered_writes_before,
              "replacement CWheel storage received stale restoration writes");
        Check(std::any_of(
                  log.entries.begin(), log.entries.end(),
                  [](const auto& entry) {
                      return entry.first == "restore-identity-replaced";
                  }),
              "replacement wheel generation was not diagnosed during restore");
    }

    {
        Host host;
        WheelAccess access;
        LogSink log;
        auto configuration = MakeConfiguration(3, 0x6E4E0004U);
        const auto original = InitialFlags(configuration.expected_wheel_count);
        access.flags = original;
        access.supports_wheel_generation_token = true;
        host.vehicles = {{304, configuration.model_hash, 77}};
        ConfigurationCatalog catalog;
        catalog.active.emplace(configuration.model_hash, configuration);
        AxleRuntime runtime(host, access, log);
        Check(runtime.Start(std::move(catalog), resolver),
              "generation-read retry fixture failed to start");
        runtime.Service(std::chrono::steady_clock::now());
        access.fail_next_generation_read = true;
        runtime.Shutdown();
        Check(runtime.State() == RuntimeState::Faulted &&
                  runtime.TrackedVehicleCount() == 1 && access.IsResolved(),
              "transient generation read failure discarded a retryable baseline");
        runtime.Shutdown();
        Check(runtime.State() == RuntimeState::Stopped &&
                  runtime.TrackedVehicleCount() == 0 &&
                  access.flags == original && !access.IsResolved(),
              "generation-token retry did not restore the original baseline");
    }
}

void TestIntentionalPhysicalOrderOverride() {
    auto configuration = MakeConfiguration(3, 0x5A17B055U);
    std::swap(configuration.axles[0], configuration.axles[1]);
    for (std::size_t position = 0; position < configuration.axles.size(); ++position) {
        auto& axle = configuration.axles[position];
        axle.order = static_cast<std::uint32_t>(position);
        axle.role = position == 0U ? "front" :
                    position + 1U == configuration.axles.size() ? "rear" : "middle";
        axle.steered = position != 1U;
        axle.powered = position == 1U;
    }
    Check(!ValidateConfiguration(configuration, "1.0.0").empty(),
          "noncanonical physical order was accepted without an override");

    IntentionalLayoutOverride layout;
    layout.mode = "visual_instancing_remap";
    layout.physical_bone_pairs = {
        {"wheel_lm1", "wheel_rm1"},
        {"wheel_lf", "wheel_rf"},
        {"wheel_lr", "wheel_rr"},
    };
    layout.bone_position_sha256 = std::string(64U, '0');
    layout.reason = "Author-reviewed single/dual/single wheel-family layout";
    configuration.intentional_layout_override = layout;
    Check(!ValidateConfiguration(configuration, "2.1.0").empty(),
          "custom physical order accepted an authored 1.0.0 runtime floor");
    configuration.minimum_runtime_version = kIntentionalLayoutMinimumRuntime;
    Check(!ValidateConfiguration(configuration, "2.0.0").empty(),
          "runtime 2.0.0 accepted a custom physical-order configuration");
    Check(ValidateConfiguration(configuration, "2.1.0").empty(),
          "valid intentional physical-order override was rejected");

    auto signed_layout = configuration;
    PromoteToSignedSchema(signed_layout);
    signed_layout.minimum_runtime_version =
        kIntentionalLayoutMinimumRuntime;
    signed_layout.axles.front().steering_gain = 0.8;
    signed_layout.steering_calculation->physical_bone_pairs =
        layout.physical_bone_pairs;
    Check(ValidateConfiguration(signed_layout, "2.1.0").empty(),
          "signed steering evidence bound to the override order was rejected");
    auto stale_signed_layout = signed_layout;
    std::swap(
        stale_signed_layout.steering_calculation->physical_bone_pairs[0],
        stale_signed_layout.steering_calculation->physical_bone_pairs[1]);
    Check(!ValidateConfiguration(stale_signed_layout, "2.1.0").empty(),
          "signed steering accepted stale physical-order evidence");
    auto stale_signed_digest = signed_layout;
    stale_signed_digest.steering_calculation->bone_position_sha256 =
        std::string(64U, '1');
    Check(!ValidateConfiguration(stale_signed_digest, "2.1.0").empty(),
          "signed steering accepted a layout digest mismatch");
    auto unexpected_signed_pairs = MakeConfiguration(3, 0x5A17B056U);
    PromoteToSignedSchema(unexpected_signed_pairs);
    unexpected_signed_pairs.axles.front().steering_gain = 0.8;
    unexpected_signed_pairs.steering_calculation->physical_bone_pairs =
        layout.physical_bone_pairs;
    Check(!ValidateConfiguration(unexpected_signed_pairs, "2.0.0").empty(),
          "signed steering accepted physical-order evidence without a layout override");

    Host host;
    host.vehicles = {{91, configuration.model_hash, 1}};
    WheelAccess access;
    access.flags = InitialFlags(configuration.expected_wheel_count);
    const auto before = access.flags;
    LogSink log;
    ConfigurationCatalog catalog;
    catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime runtime(host, access, log);
    Resolver resolver;
    Check(runtime.Start(std::move(catalog), resolver),
          "intentional-layout runtime did not start");
    const auto first_service = std::chrono::steady_clock::now();
    runtime.Service(first_service);
    VerifyDesiredFlags(configuration, before, access.flags);
    runtime.Shutdown();

    auto stale = configuration;
    stale.intentional_layout_override->physical_bone_pairs[0] =
        {"wheel_lf", "wheel_rf"};
    Check(!ValidateConfiguration(stale, "2.1.0").empty(),
          "stale intentional layout mapping was accepted");
}

void TestInvalidWheelCountAndRollback() {
    Host host;
    WheelAccess access;
    LogSink log;
    Resolver resolver;
    auto configuration = MakeConfiguration(3);
    host.vehicles = {{7, configuration.model_hash, 1}};
    access.flags = InitialFlags(4);
    const auto four_wheel_before = access.flags;
    ConfigurationCatalog catalog;
    catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime runtime(host, access, log);
    Check(runtime.Start(std::move(catalog), resolver), "runtime start failed");
    runtime.Service(std::chrono::steady_clock::now());
    Check(access.flags == four_wheel_before,
          "wheel-count mismatch performed a write");
    runtime.Shutdown();

    Host rollback_host;
    WheelAccess rollback_access;
    LogSink rollback_log;
    auto rollback_configuration = MakeConfiguration(3);
    rollback_host.vehicles = {{8, rollback_configuration.model_hash, 1}};
    rollback_access.flags = InitialFlags(6);
    const auto rollback_before = rollback_access.flags;
    // Reverse mapping causes several changes; fail one write once and require
    // the transaction to restore every preceding write.
    rollback_access.fail_next_write_at = 1;
    ConfigurationCatalog rollback_catalog;
    rollback_catalog.active.emplace(rollback_configuration.model_hash,
                                    rollback_configuration);
    AxleRuntime rollback_runtime(rollback_host, rollback_access, rollback_log);
    Check(rollback_runtime.Start(std::move(rollback_catalog), resolver),
          "rollback runtime start failed");
    rollback_runtime.Service(std::chrono::steady_clock::now());
    Check(rollback_access.flags == rollback_before,
          "partial application did not roll back");
    Check(rollback_runtime.TrackedVehicleCount() == 0,
          "failed entity remained tracked");
    rollback_runtime.Shutdown();
}

void TestRecoveryRetainsOriginalBaseline() {
    Host host;
    WheelAccess access;
    LogSink log;
    Resolver resolver;
    auto configuration = MakeConfiguration(3);
    const auto original = InitialFlags(6);
    access.flags = original;
    host.vehicles = {{31, configuration.model_hash, 7}};
    ConfigurationCatalog catalog;
    catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime runtime(host, access, log);
    Check(runtime.Start(std::move(catalog), resolver),
          "recovery fixture failed to start");
    const auto first = std::chrono::steady_clock::now();
    runtime.Service(first);
    Check(runtime.TrackedVehicleCount() == 1,
          "initial application did not retain its baseline");

    // Drift one managed value away from both the authored value and the
    // startup baseline, then fail the recovery write.  The recovery rollback
    // restores this drift value; shutdown must still use the startup baseline.
    access.flags[0] = static_cast<std::uint16_t>(access.flags[0] & ~0x18U);
    access.fail_next_write_at = 0;
    runtime.Service(first + std::chrono::seconds(3));
    Check(runtime.TrackedVehicleCount() == 1,
          "failed recovery discarded the startup baseline");
    runtime.Shutdown();
    Check(runtime.State() == RuntimeState::Stopped && access.flags == original,
          "shutdown restored a recovery snapshot instead of startup state");
}

void TestShutdownRestorationRetries() {
    Resolver resolver;
    for (const bool fail_read : {true, false}) {
        Host host;
        WheelAccess access;
        LogSink log;
        auto configuration = MakeConfiguration(3);
        const auto original = InitialFlags(6);
        access.flags = original;
        host.vehicles = {{41, configuration.model_hash, 9}};
        ConfigurationCatalog catalog;
        catalog.active.emplace(configuration.model_hash, configuration);
        AxleRuntime runtime(host, access, log);
        Check(runtime.Start(std::move(catalog), resolver),
              "shutdown retry fixture failed to start");
        runtime.Service(std::chrono::steady_clock::now());
        if (fail_read) {
            access.fail_next_read_at = 0;
        } else {
            access.fail_next_write_at = 0;
        }
        runtime.Shutdown();
        Check(runtime.State() == RuntimeState::Faulted &&
                  runtime.TrackedVehicleCount() == 1 && access.IsResolved(),
              "failed shutdown restoration did not remain retryable");
        runtime.Shutdown();
        Check(runtime.State() == RuntimeState::Stopped &&
                  runtime.TrackedVehicleCount() == 0 &&
                  access.flags == original && !access.IsResolved(),
              "second shutdown did not complete retained restoration");
    }
}

void TestShutdownReleasesObsoleteVehicleIdentities() {
    Resolver resolver;
    for (const int replacement_case : {0, 1, 2}) {
        Host host;
        WheelAccess access;
        LogSink log;
        auto configuration = MakeConfiguration(3);
        access.flags = InitialFlags(6);
        host.vehicles = {{51, configuration.model_hash, 12}};
        ConfigurationCatalog catalog;
        catalog.active.emplace(configuration.model_hash, configuration);
        AxleRuntime runtime(host, access, log);
        Check(runtime.Start(std::move(catalog), resolver),
              "obsolete identity fixture failed to start");
        runtime.Service(std::chrono::steady_clock::now());
        Check(runtime.TrackedVehicleCount() == 1,
              "obsolete identity fixture did not retain its baseline");
        const auto flag_writes = access.writes;
        const auto power_writes = access.powered_writes;

        if (replacement_case == 0) {
            host.vehicles.clear();
        } else if (replacement_case == 1) {
            ++host.vehicles.front().wheel_generation;
        } else {
            host.vehicles.front().model_hash ^= 0x01010101U;
        }

        runtime.Shutdown();
        Check(runtime.State() == RuntimeState::Stopped &&
                  runtime.TrackedVehicleCount() == 0 && !access.IsResolved(),
              "obsolete vehicle identity kept shutdown faulted");
        Check(access.writes == flag_writes &&
                  access.powered_writes == power_writes,
              "obsolete vehicle identity was touched during shutdown");
        const std::string expected_code =
            replacement_case == 0
                ? "restore-entity-released"
                : "restore-identity-replaced";
        Check(std::any_of(
                  log.entries.begin(), log.entries.end(),
                  [&](const auto& entry) {
                      return entry.first == expected_code;
                  }),
              "obsolete vehicle identity release was not diagnosed");
    }
}

void TestOnlineAndUnsupportedGuards() {
    Host host;
    host.online = true;
    WheelAccess access;
    LogSink log;
    Resolver resolver;
    auto configuration = MakeConfiguration(2);
    access.flags = InitialFlags(4);
    ConfigurationCatalog catalog;
    catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime runtime(host, access, log);
    Check(!runtime.Start(std::move(catalog), resolver),
          "online guard allowed startup");
    Check(runtime.State() == RuntimeState::DisabledOnline,
          "online guard did not select disabled state");
    Check(access.resolve_calls == 0 && access.writes == 0,
          "online guard reached adapter resolution or writes");
    runtime.Shutdown();

    Host boundary_host;
    WheelAccess boundary_access;
    LogSink boundary_log;
    auto boundary_configuration = MakeConfiguration(2);
    boundary_access.flags = InitialFlags(4);
    boundary_host.vehicles = {
        {2, boundary_configuration.model_hash, 1},
    };
    // Start, Service, and Apply each observe Story Mode.  The fourth check is
    // the write-boundary guard after all reads have completed.
    boundary_host.online_on_check = 4;
    ConfigurationCatalog boundary_catalog;
    boundary_catalog.active.emplace(boundary_configuration.model_hash,
                                    boundary_configuration);
    AxleRuntime boundary_runtime(
        boundary_host, boundary_access, boundary_log);
    Check(boundary_runtime.Start(std::move(boundary_catalog), resolver),
          "write-boundary fixture failed to start");
    boundary_runtime.Service(std::chrono::steady_clock::now());
    Check(boundary_runtime.State() == RuntimeState::DisabledOnline &&
              boundary_access.writes == 0 &&
              boundary_access.powered_writes == 0,
          "online transition between reads and writes crossed the guard");
    boundary_runtime.Shutdown();

    LegacyWheelAccess real_adapter;
    GameIdentity game{Edition::Legacy, 9999, "fixture"};
    Check(!real_adapter.IsSupportedBuild(game),
          "empty compatibility adapter claimed a supported build");
    Check(!real_adapter.Resolve(game, resolver) && !real_adapter.IsResolved(),
          "empty compatibility adapter resolved unexpectedly");
    std::uint16_t flags = 0;
    Check(!real_adapter.ReadWheelFlags({1, 1, 1}, 0, flags),
          "unresolved adapter allowed a memory read");
    Check(!real_adapter.WriteWheelFlags({1, 1, 1}, 0, flags),
          "unresolved adapter allowed a memory write");

    auto profile = std::make_shared<CompiledProfile>();
    LegacyWheelAccess profiled_adapter({profile});
    Resolver validating_resolver;
    validating_resolver.allow = true;
    Check(profiled_adapter.IsSupportedBuild(game),
          "exact compiled profile was not recognized");
    Check(profiled_adapter.Resolve(game, validating_resolver) &&
              profiled_adapter.IsResolved() &&
              profiled_adapter.MaximumPhysicalAxles() == 5,
          "validated signature profile did not bind");
    Check(profiled_adapter.WriteWheelFlags({1, 1, 1}, 0, 0xA408U) &&
              profiled_adapter.ReadWheelFlags({1, 1, 1}, 0, flags) &&
              flags == 0xA408U,
          "bound build profile did not service wheel access");
    std::uint64_t generation = 0;
    Check(profiled_adapter.SupportsWheelGenerationToken() &&
              profiled_adapter.ReadWheelGenerationToken(
                  {1, 1, 1}, generation) &&
              generation == (profile->generation_token ^ 1U),
          "bound build profile did not forward its wheel generation token");
    double static_force = 0.0;
    Check(!profiled_adapter.SupportsStaticForce() &&
              !profiled_adapter.ReadWheelStaticForce(
                  {1, 1, 1}, 0, static_force) &&
              !profiled_adapter.WriteWheelStaticForce(
                  {1, 1, 1}, 0, 1.0),
          "profile without reviewed StaticForce access exposed the capability");
    profiled_adapter.Reset();
    Check(!profiled_adapter.IsResolved() && !profile->bound,
          "profile reset retained resolved accessors");
}

void TestSignedSteeringGainCapabilityAndRestore() {
    Resolver resolver;
    auto configuration = MakeConfiguration(3);
    PromoteToSignedSchema(configuration);
    configuration.axles[0].steering_gain = 1.0;
    configuration.axles[1].steering_gain = 0.0;
    configuration.axles[2].steering_gain = -0.22;

    Host unsupported_host;
    unsupported_host.vehicles = {{80, configuration.model_hash, 1}};
    WheelAccess unsupported_access;
    unsupported_access.flags = InitialFlags(6);
    LogSink unsupported_log;
    ConfigurationCatalog unsupported_catalog;
    unsupported_catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime unsupported_runtime(
        unsupported_host, unsupported_access, unsupported_log);
    Check(!unsupported_runtime.Start(std::move(unsupported_catalog), resolver),
          "flag-only profile accepted signed steering gain");
    Check(unsupported_access.writes == 0 && unsupported_access.gain_writes == 0,
          "unsupported steering gain reached a wheel write");
    Check(std::any_of(
              unsupported_log.entries.begin(), unsupported_log.entries.end(),
              [](const auto& entry) {
                  return entry.first == "steering-gain-capability-missing";
              }),
          "unsupported steering gain did not report its capability blocker");
    unsupported_runtime.Shutdown();

    Host host;
    host.vehicles = {{81, configuration.model_hash, 2}};
    WheelAccess access;
    access.supports_steering_gain = true;
    access.flags = InitialFlags(6);
    access.steering_gains = {0.11, 0.12, 0.13, 0.14, 0.15, 0.16};
    const auto gains_before = access.steering_gains;
    LogSink log;
    ConfigurationCatalog catalog;
    catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime runtime(host, access, log);
    Check(runtime.Start(std::move(catalog), resolver),
          "gain-capable profile did not start");
    runtime.Service(std::chrono::steady_clock::now());
    for (const auto& axle : configuration.axles) {
        const double expected = axle.steering_gain.value_or(
            axle.steered ? 1.0 : 0.0);
        const auto left = configuration.wheel_index_map.at(axle.left_bone);
        const auto right = configuration.wheel_index_map.at(axle.right_bone);
        if (axle.steered) {
            Check(std::abs(access.steering_gains.at(left) - expected) <
                      0.000001 &&
                      std::abs(access.steering_gains.at(right) - expected) <
                          0.000001,
                  "signed steering gain did not follow the canonical wheel map");
        } else {
            Check(access.steering_gains.at(left) == gains_before.at(left) &&
                      access.steering_gains.at(right) == gains_before.at(right),
                  "fixed axle steering gain was unnecessarily managed");
        }
    }
    runtime.Shutdown();
    Check(access.steering_gains == gains_before,
          "shutdown did not restore original steering gain");

    Host independent_restore_host;
    independent_restore_host.vehicles = {{83, configuration.model_hash, 4}};
    WheelAccess independent_restore_access;
    independent_restore_access.supports_steering_gain = true;
    independent_restore_access.flags = InitialFlags(6);
    independent_restore_access.steering_gains = gains_before;
    LogSink independent_restore_log;
    ConfigurationCatalog independent_restore_catalog;
    independent_restore_catalog.active.emplace(
        configuration.model_hash, configuration);
    AxleRuntime independent_restore_runtime(
        independent_restore_host, independent_restore_access,
        independent_restore_log);
    Check(independent_restore_runtime.Start(
              std::move(independent_restore_catalog), resolver),
          "independent gain restore fixture did not start");
    independent_restore_runtime.Service(std::chrono::steady_clock::now());
    independent_restore_access.fail_next_read_at = 0;
    independent_restore_runtime.Shutdown();
    Check(independent_restore_runtime.State() == RuntimeState::Faulted &&
              independent_restore_access.steering_gains == gains_before,
          "flag read failure prevented independent gain restoration");
    independent_restore_runtime.Shutdown();
    Check(independent_restore_runtime.State() == RuntimeState::Stopped,
          "independent gain restore retry did not complete");

    Host rollback_host;
    rollback_host.vehicles = {{82, configuration.model_hash, 3}};
    WheelAccess rollback_access;
    rollback_access.supports_steering_gain = true;
    rollback_access.flags = InitialFlags(6);
    rollback_access.steering_gains = gains_before;
    const auto flags_before = rollback_access.flags;
    rollback_access.fail_next_gain_write_at = 1;
    LogSink rollback_log;
    ConfigurationCatalog rollback_catalog;
    rollback_catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime rollback_runtime(
        rollback_host, rollback_access, rollback_log);
    Check(rollback_runtime.Start(std::move(rollback_catalog), resolver),
          "gain rollback runtime did not start");
    rollback_runtime.Service(std::chrono::steady_clock::now());
    Check(rollback_access.flags == flags_before &&
              rollback_access.steering_gains == gains_before,
          "failed steering gain transaction did not restore all wheel state");
    Check(rollback_runtime.TrackedVehicleCount() == 0,
          "failed steering gain transaction remained tracked");
    rollback_runtime.Shutdown();

    auto inverted = configuration;
    inverted.schema_version = kAxleSchemaVersion;
    inverted.minimum_runtime_version = kSteeringPolarityMinimumRuntime;
    inverted.steering_command_polarity = "inverted";
    Check(ValidateConfiguration(inverted, "4.0.0").empty(),
          "valid inverted steering command polarity was rejected");
    Host inverted_host;
    inverted_host.vehicles = {{84, inverted.model_hash, 5}};
    WheelAccess inverted_access;
    inverted_access.supports_steering_gain = true;
    inverted_access.flags = InitialFlags(6);
    inverted_access.steering_gains = gains_before;
    LogSink inverted_log;
    ConfigurationCatalog inverted_catalog;
    inverted_catalog.active.emplace(inverted.model_hash, inverted);
    AxleRuntime inverted_runtime(
        inverted_host, inverted_access, inverted_log);
    Check(inverted_runtime.Start(std::move(inverted_catalog), resolver),
          "inverted-polarity runtime did not start");
    inverted_runtime.Service(std::chrono::steady_clock::now());
    for (const auto& axle : inverted.axles) {
        const double base = axle.steering_gain.value_or(
            axle.steered ? 1.0 : 0.0);
        const double expected = -base;
        const auto left = inverted.wheel_index_map.at(axle.left_bone);
        const auto right = inverted.wheel_index_map.at(axle.right_bone);
        if (axle.steered) {
            Check(std::abs(inverted_access.steering_gains.at(left) - expected) <
                      0.000001 &&
                      std::abs(inverted_access.steering_gains.at(right) - expected) <
                          0.000001,
                  "steering command polarity was not applied exactly once");
        } else {
            Check(inverted_access.steering_gains.at(left) ==
                      gains_before.at(left) &&
                      inverted_access.steering_gains.at(right) ==
                          gains_before.at(right),
                  "inverted polarity managed a fixed axle gain");
        }
    }
    Check(std::any_of(
              inverted_log.entries.begin(), inverted_log.entries.end(),
              [](const auto& entry) {
                  return entry.first == "steering-gain-resolved" &&
                         entry.second.find("polarity=inverted") !=
                             std::string::npos &&
                         entry.second.find("effective=") != std::string::npos;
              }),
          "polarity log omitted base/polarity/effective steering evidence");
    inverted_runtime.Shutdown();

}

void TestVolatileSteeringMaintenanceIgnoresFixedWheels() {
    auto configuration = MakeConfiguration(3, 0x5A17B060U);
    PromoteToSignedSchema(configuration);
    configuration.axles[0].steering_gain = 1.0;
    configuration.axles[1].steering_gain = 0.0;
    configuration.axles[2].steering_gain = -0.22;

    Host host;
    host.vehicles = {{85, configuration.model_hash, 1}};
    WheelAccess access;
    access.supports_steering_gain = true;
    access.flags = InitialFlags(6);
    access.steering_gains = {0.11, 0.12, 0.13, 0.14, 0.15, 0.16};
    LogSink log;
    ConfigurationCatalog catalog;
    catalog.active.emplace(configuration.model_hash, configuration);
    Resolver resolver;
    AxleRuntime runtime(host, access, log);
    Check(runtime.Start(std::move(catalog), resolver),
          "volatile steering maintenance fixture did not start");
    const auto first = std::chrono::steady_clock::now();
    runtime.Service(first);

    const auto managed =
        configuration.wheel_index_map.at(configuration.axles[0].left_bone);
    const auto fixed =
        configuration.wheel_index_map.at(configuration.axles[1].left_bone);
    access.steering_gains[managed] = 13.7362;
    access.steering_gains[fixed] = 7.25;
    const auto writes_before = access.gain_writes;

    // Less than the discovery interval: only the bounded per-frame volatile
    // maintenance path can repair this engine-owned field.
    runtime.Service(first + std::chrono::milliseconds(1));
    Check(std::abs(access.steering_gains[managed] - 1.0) < 0.000001,
          "per-frame maintenance did not repair GTA steering gain drift");
    Check(access.steering_gains[fixed] == 7.25,
          "fixed wheel gain drift was treated as controller-owned state");
    Check(access.gain_writes == writes_before + 1U,
          "volatile maintenance wrote more than the one drifting managed wheel");

    access.steering_gains[managed] = 9.0;
    runtime.Service(first + std::chrono::milliseconds(2));
    Check(std::count_if(
              log.entries.begin(), log.entries.end(),
              [](const auto& entry) {
                  return entry.first == "steering-gain-reasserted";
              }) == 1,
          "repeated gain drift produced frame-spam logging");
    runtime.Shutdown();
}

void TestRuntimeGeometryUsesAuthoritativePhysicalOrder() {
    auto configuration = MakeConfiguration(3, 0x5A17B061U);
    std::swap(configuration.axles[0], configuration.axles[1]);
    for (std::size_t position = 0;
         position < configuration.axles.size(); ++position) {
        auto& axle = configuration.axles[position];
        axle.order = static_cast<std::uint32_t>(position);
        axle.role = position == 0U
                        ? "front"
                        : (position + 1U == configuration.axles.size()
                               ? "rear"
                               : "middle");
        axle.steered = position != 1U;
        axle.powered = position == 1U;
    }
    IntentionalLayoutOverride layout;
    layout.mode = "visual_instancing_remap";
    layout.physical_bone_pairs = {
        {"wheel_lm1", "wheel_rm1"},
        {"wheel_lf", "wheel_rf"},
        {"wheel_lr", "wheel_rr"},
    };
    layout.bone_position_sha256 = std::string(64U, '0');
    layout.reason = "Author-reviewed single/dual/single wheel-family layout";
    configuration.intentional_layout_override = layout;
    PromoteToSignedSchema(configuration);
    configuration.minimum_runtime_version = kRuntimeGeometryMinimumRuntime;
    configuration.axles[0].steering_gain = 0.179;
    configuration.axles[1].steering_gain = 0.0;
    configuration.axles[2].steering_gain = -1.0;
    auto& evidence = *configuration.steering_calculation;
    evidence.mode = "automaticGeometry";
    evidence.runtime_recompute = true;
    evidence.reference_selection = "farthest_steered_axle";
    evidence.pivot_longitudinal_position = -4.0748;
    evidence.pivot_source = "selected_fixed_axles";
    evidence.pivot_axle_orders = {1};
    evidence.reference_axle_order = 2;
    evidence.reference_lock_degrees = 35.0;
    evidence.pair_position_tolerance = 0.01;
    evidence.position_epsilon = 0.0001;
    evidence.physical_bone_pairs = layout.physical_bone_pairs;
    Check(ValidateConfiguration(configuration, "4.1.0").empty(),
          "runtime geometry fixture did not validate");

    Host unsupported_host;
    WheelAccess unsupported_access;
    unsupported_access.supports_steering_gain = true;
    unsupported_access.flags = InitialFlags(6);
    unsupported_access.steering_gains =
        {0.11, 0.12, 0.13, 0.14, 0.15, 0.16};
    ConfigurationCatalog unsupported_catalog;
    unsupported_catalog.active.emplace(configuration.model_hash, configuration);
    LogSink unsupported_log;
    Resolver resolver;
    AxleRuntime unsupported_runtime(
        unsupported_host, unsupported_access, unsupported_log);
    Check(!unsupported_runtime.Start(
              std::move(unsupported_catalog), resolver) &&
              unsupported_access.gain_writes == 0,
          "runtime geometry ran without authoritative position capability");
    unsupported_runtime.Shutdown();

    Host host;
    host.vehicles = {{86, configuration.model_hash, 1}};
    WheelAccess access;
    access.supports_steering_gain = true;
    access.supports_wheel_local_position = true;
    access.flags = InitialFlags(6);
    access.steering_gains = {0.11, 0.12, 0.13, 0.14, 0.15, 0.16};
    const auto gains_before = access.steering_gains;
    access.wheel_local_positions.resize(6);
    const auto set_pair = [&](const AxleDefinition& axle,
                              double longitudinal) {
        const auto left = configuration.wheel_index_map.at(axle.left_bone);
        const auto right = configuration.wheel_index_map.at(axle.right_bone);
        access.wheel_local_positions[left] =
            {-1.0, longitudinal, 0.0};
        access.wheel_local_positions[right] =
            {1.0, longitudinal, 0.0};
    };
    set_pair(configuration.axles[0], 4.4533);
    set_pair(configuration.axles[1], -4.0748);
    set_pair(configuration.axles[2], -5.4140);

    LogSink log;
    ConfigurationCatalog catalog;
    catalog.active.emplace(configuration.model_hash, configuration);
    AxleRuntime runtime(host, access, log);
    Check(runtime.Start(std::move(catalog), resolver),
          "authoritative runtime geometry fixture did not start");
    runtime.Service(std::chrono::steady_clock::now());

    const auto front_left =
        configuration.wheel_index_map.at(configuration.axles[0].left_bone);
    const auto front_right =
        configuration.wheel_index_map.at(configuration.axles[0].right_bone);
    const auto fixed_left =
        configuration.wheel_index_map.at(configuration.axles[1].left_bone);
    const auto fixed_right =
        configuration.wheel_index_map.at(configuration.axles[1].right_bone);
    const auto rear_left =
        configuration.wheel_index_map.at(configuration.axles[2].left_bone);
    const auto rear_right =
        configuration.wheel_index_map.at(configuration.axles[2].right_bone);
    const double lock = 35.0 * std::acos(-1.0) / 180.0;
    const double radius = (4.4533 - (-4.0748)) / std::tan(lock);
    const double expected_rear =
        std::atan((-5.4140 - (-4.0748)) / radius) / lock;
    Check(std::abs(access.steering_gains[front_left] - 1.0) < 0.000001 &&
              std::abs(access.steering_gains[front_right] - 1.0) < 0.000001,
          "physical front was not selected as the farthest reference axle");
    Check(access.steering_gains[fixed_left] == gains_before[fixed_left] &&
              access.steering_gains[fixed_right] == gains_before[fixed_right],
          "runtime geometry wrote gain state to the fixed physical axle");
    Check(std::abs(access.steering_gains[rear_left] - expected_rear) <
                  0.000001 &&
              std::abs(access.steering_gains[rear_right] - expected_rear) <
                  0.000001 &&
              std::abs(expected_rear + 0.179) < 0.01,
          "physical rear did not receive the expected bounded counter-steer gain");
    Check(access.position_reads == 6,
          "runtime geometry did not read exactly one authoritative position per wheel");
    Check(std::any_of(
              log.entries.begin(), log.entries.end(),
              [](const auto& entry) {
                  return entry.first ==
                         "runtime-steering-geometry-corrected";
              }),
          "runtime geometry correction was not reported");
    runtime.Shutdown();
}

void TestAxleSupportBiasApplyRollbackAndRestore() {
    auto configuration = MakeConfiguration(3);
    PromoteToSupportBias(configuration, {1.25, 1.0, 0.75});
    Check(ValidateConfiguration(configuration, "3.0.0").empty(),
          "valid schema-3 support bias was rejected");

    Host host;
    host.supports_physics_activation = true;
    WheelAccess access;
    access.supports_static_force = true;
    access.flags = InitialFlags(6);
    access.static_forces = {10.0, 20.0, 30.0, 40.0, 50.0, 60.0};
    const auto original_flags = access.flags;
    const auto original_forces = access.static_forces;
    host.vehicles = {{42, configuration.model_hash, 7}};
    ConfigurationCatalog catalog;
    catalog.active.emplace(configuration.model_hash, configuration);
    Resolver resolver;
    LogSink log;
    AxleRuntime runtime(host, access, log);
    Check(runtime.Start(std::move(catalog), resolver),
          "support-bias runtime did not start");
    const auto first_service = std::chrono::steady_clock::now();
    runtime.Service(first_service);
    // The exported map places rear, middle, then front at indices 0..5.
    // Bias scales each wheel's original load and normalizes globally; it must
    // not flatten an axle's existing left/right distribution.
    const std::vector<double> wheel_weights{
        0.75, 0.75, 1.0, 1.0, 1.25, 1.25};
    double weighted_total = 0.0;
    double original_total = 0.0;
    for (std::size_t index = 0; index < original_forces.size(); ++index) {
        weighted_total += original_forces[index] * wheel_weights[index];
        original_total += original_forces[index];
    }
    const double normalization = original_total / weighted_total;
    std::vector<double> expected;
    for (std::size_t index = 0; index < original_forces.size(); ++index) {
        expected.push_back(
            original_forces[index] * wheel_weights[index] * normalization);
    }
    Check(access.static_forces.size() == expected.size(),
          "support-bias wheel count changed");
    for (std::size_t index = 0; index < expected.size(); ++index) {
        Check(std::abs(access.static_forces[index] - expected[index]) < 1.0e-8,
              "normalized support bias did not follow the mapped physical axle");
    }
    double applied_total = 0.0;
    for (const auto value : access.static_forces) applied_total += value;
    Check(std::abs(original_total - applied_total) < 1.0e-8,
          "support bias changed total vehicle StaticForce");
    for (std::size_t left = 0; left < expected.size(); left += 2U) {
        Check(std::abs(
                  access.static_forces[left] /
                      access.static_forces[left + 1U] -
                  original_forces[left] / original_forces[left + 1U]) <
                  1.0e-8,
              "support bias flattened an axle's original left/right load distribution");
    }
    Check(host.physics_activation_calls == 1,
          "support-bias apply did not activate physics exactly once");

    for (auto& force : access.static_forces) force *= 2.0;
    runtime.Service(first_service + std::chrono::seconds(3));
    for (std::size_t index = 0; index < expected.size(); ++index) {
        Check(std::abs(access.static_forces[index] - expected[index]) < 1.0e-8,
              "support recovery drifted from the original total StaticForce");
    }
    Check(host.physics_activation_calls == 2,
          "support recovery did not reactivate physics");

    runtime.Shutdown();
    Check(access.static_forces == original_forces &&
              access.flags == original_flags,
          "support-bias shutdown did not restore original wheel state");
    Check(host.physics_activation_calls == 3,
          "support-bias restoration did not reactivate physics");

    Host unsupported_host;
    WheelAccess unsupported_access;
    unsupported_access.flags = original_flags;
    unsupported_access.static_forces = original_forces;
    unsupported_host.vehicles = {{43, configuration.model_hash, 1}};
    ConfigurationCatalog unsupported_catalog;
    unsupported_catalog.active.emplace(configuration.model_hash, configuration);
    LogSink unsupported_log;
    AxleRuntime unsupported_runtime(
        unsupported_host, unsupported_access, unsupported_log);
    Check(!unsupported_runtime.Start(
              std::move(unsupported_catalog), resolver),
          "support bias started without validated StaticForce capability");
    Check(unsupported_access.static_force_writes == 0,
          "unsupported profile attempted a StaticForce write");

    Host activation_host;
    activation_host.supports_physics_activation = true;
    activation_host.physics_activation_succeeds = false;
    WheelAccess activation_access;
    activation_access.supports_static_force = true;
    activation_access.flags = original_flags;
    activation_access.static_forces = original_forces;
    activation_host.vehicles = {{45, configuration.model_hash, 1}};
    ConfigurationCatalog activation_catalog;
    activation_catalog.active.emplace(configuration.model_hash, configuration);
    LogSink activation_log;
    AxleRuntime activation_runtime(
        activation_host, activation_access, activation_log);
    Check(activation_runtime.Start(std::move(activation_catalog), resolver),
          "physics-activation rollback runtime did not start");
    activation_runtime.Service(std::chrono::steady_clock::now());
    Check(activation_access.static_forces == original_forces,
          "physics activation failure did not restore StaticForce values");
    Check(activation_runtime.TrackedVehicleCount() == 1,
          "failed rollback activation discarded the restoration baseline");
    activation_host.physics_activation_succeeds = true;
    activation_runtime.Shutdown();
    Check(activation_runtime.TrackedVehicleCount() == 0,
          "restoration retry did not clear the support baseline");

    Host rollback_host;
    rollback_host.supports_physics_activation = true;
    WheelAccess rollback_access;
    rollback_access.supports_static_force = true;
    rollback_access.flags = original_flags;
    rollback_access.static_forces = original_forces;
    rollback_access.fail_next_static_write_at = 2;
    rollback_host.vehicles = {{44, configuration.model_hash, 1}};
    ConfigurationCatalog rollback_catalog;
    rollback_catalog.active.emplace(configuration.model_hash, configuration);
    LogSink rollback_log;
    AxleRuntime rollback_runtime(
        rollback_host, rollback_access, rollback_log);
    Check(rollback_runtime.Start(std::move(rollback_catalog), resolver),
          "support rollback runtime did not start");
    rollback_runtime.Service(std::chrono::steady_clock::now());
    Check(rollback_access.static_forces == original_forces &&
              rollback_access.flags == original_flags,
          "failed support-bias transaction did not restore all wheel state");
    Check(rollback_runtime.TrackedVehicleCount() == 0,
          "completed support rollback retained a stale baseline");
    rollback_runtime.Shutdown();

    Host verification_host;
    verification_host.supports_physics_activation = true;
    WheelAccess verification_access;
    verification_access.supports_static_force = true;
    verification_access.flags = original_flags;
    verification_access.static_forces = original_forces;
    // Six construction reads succeed; the first post-activation readback is
    // corrupted once to exercise verified rollback.
    verification_access.corrupt_static_read_number = 6;
    verification_host.vehicles = {{46, configuration.model_hash, 1}};
    ConfigurationCatalog verification_catalog;
    verification_catalog.active.emplace(
        configuration.model_hash, configuration);
    LogSink verification_log;
    AxleRuntime verification_runtime(
        verification_host, verification_access, verification_log);
    Check(verification_runtime.Start(
              std::move(verification_catalog), resolver),
          "support verification runtime did not start");
    verification_runtime.Service(std::chrono::steady_clock::now());
    Check(verification_access.static_forces == original_forces &&
              verification_runtime.TrackedVehicleCount() == 0,
          "failed StaticForce verification did not complete rollback");
    verification_runtime.Shutdown();
}

void TestAxleSupportBiasParsingAndValidation() {
    const std::string json_text = R"json({
      "schemaVersion":3,
      "configurationId":"support-fixture",
      "modelName":"support_fixture",
      "modelHash":"0x10203040",
      "expectedWheelCount":4,
      "minimumRuntimeVersion":"3.0.0",
      "wheelIndexMap":{"wheel_lf":0,"wheel_rf":1,"wheel_lr":2,"wheel_rr":3},
      "axles":[
        {"order":0,"role":"front","leftBone":"wheel_lf","rightBone":"wheel_rf","steered":true,"powered":false,"steeringGain":1.0,"suspension":{"supportWeight":1.25}},
        {"order":1,"role":"rear","leftBone":"wheel_lr","rightBone":"wheel_rr","steered":false,"powered":true,"steeringGain":0.0,"suspension":{"supportWeight":0.75}}
      ],
      "compatibility":{"storyLegacy":true}
    })json";
    std::vector<ValidationIssue> issues;
    const auto parsed = ParseConfigurationJson(
        json_text, "3.0.0", issues, "support.json");
    Check(parsed.has_value() && issues.empty() &&
              parsed->axles.front().suspension.has_value() &&
              parsed->axles.front().suspension->support_weight == 1.25,
          "typed supportWeight did not parse");

    auto partial = *parsed;
    partial.axles.back().suspension.reset();
    Check(!ValidateConfiguration(partial, "3.0.0").empty(),
          "partial axle support bias was accepted");
    auto below_range = *parsed;
    below_range.axles.front().suspension->support_weight = 0.749;
    Check(!ValidateConfiguration(below_range, "3.0.0").empty(),
          "out-of-range support weight was accepted");
    auto above_range = *parsed;
    above_range.axles.front().suspension->support_weight = 1.251;
    Check(!ValidateConfiguration(above_range, "3.0.0").empty(),
          "out-of-range support weight was accepted");
    auto nonfinite = *parsed;
    nonfinite.axles.front().suspension->support_weight =
        std::numeric_limits<double>::quiet_NaN();
    Check(!ValidateConfiguration(nonfinite, "3.0.0").empty(),
          "non-finite support weight was accepted");
    auto old_minimum = *parsed;
    old_minimum.minimum_runtime_version = "2.1.0";
    Check(!ValidateConfiguration(old_minimum, "3.0.0").empty(),
          "support bias accepted a pre-3.0 minimum runtime");
}

void TestRuntimeSettingsContract() {
    const std::string packaged_settings = R"json({
      "schemaVersion": 2,
      "enabled": true,
      "discoveryIntervalMs": 250,
      "recoveryIntervalMs": 2000,
      "restoreOnUnload": true,
      "configurationDirectory": "scripts/ExampleTransitPack/VehicleSettings",
      "logFile": "scripts/ExampleTransitPack/Axles.log"
    })json";
    std::vector<ValidationIssue> issues;
    const auto parsed = ParseRuntimeSettingsJson(
        packaged_settings, issues, "runtime.json");
    Check(parsed.has_value() && issues.empty(),
          "packaged native runtime settings were rejected");
    Check(parsed->enabled && parsed->discovery_interval_ms == 250U &&
              parsed->recovery_interval_ms == 2000U &&
              parsed->restore_on_unload &&
              parsed->configuration_directory ==
                  "scripts/ExampleTransitPack/VehicleSettings" &&
              parsed->log_file == "scripts/ExampleTransitPack/Axles.log",
          "packaged native runtime settings changed values while parsing");

    std::vector<ValidationIssue> legacy_path_issues;
    const auto legacy_paths = ParseRuntimeSettingsJson(
        R"json({"schemaVersion":1,"logFile":"logs/VehicleWorkbenchAxles.log"})json",
        legacy_path_issues, "legacy-runtime.json");
    Check(legacy_paths.has_value() && legacy_path_issues.empty() &&
              legacy_paths->schema_version == 1U &&
              legacy_paths->configuration_directory == "configs" &&
              legacy_paths->log_file == "logs/VehicleWorkbenchAxles.log",
          "schema-1 runtime path behavior was not preserved");

    std::vector<ValidationIssue> disabled_issues;
    const auto disabled = ParseRuntimeSettingsJson(
        R"json({"schemaVersion":1,"enabled":false})json",
        disabled_issues, "runtime.json");
    Check(disabled.has_value() && disabled_issues.empty() &&
              !disabled->enabled,
          "runtime enabled=false was not preserved");

    std::vector<ValidationIssue> default_issues;
    const auto defaulted = ParseRuntimeSettingsJson(
        R"json({"schemaVersion":1})json", default_issues,
        "runtime.json");
    Check(defaulted.has_value() && default_issues.empty() &&
              defaulted->enabled,
          "omitted runtime enabled setting did not default on");

    std::vector<ValidationIssue> typed_issues;
    Check(!ParseRuntimeSettingsJson(
               R"json({"schemaVersion":1,"enabled":1})json",
               typed_issues, "runtime.json")
               .has_value(),
          "runtime enabled accepted a non-boolean value");

    std::vector<ValidationIssue> unknown_issues;
    Check(!ParseRuntimeSettingsJson(
               R"json({"schemaVersion":1,"runtime_name":"bad-shape"})json",
               unknown_issues, "runtime.json")
               .has_value(),
          "runtime settings accepted release metadata fields");
    Check(std::any_of(
              unknown_issues.begin(), unknown_issues.end(),
              [](const ValidationIssue& issue) {
                  return issue.code == "unknown-runtime-setting" && issue.fatal;
              }),
          "unknown runtime settings were not reported fail-closed");

    std::vector<ValidationIssue> unsafe_configuration_issues;
    Check(!ParseRuntimeSettingsJson(
               R"json({"schemaVersion":1,"configurationDirectory":"../outside"})json",
               unsafe_configuration_issues, "runtime.json")
               .has_value(),
          "runtime settings accepted a parent-traversing configuration path");
    Check(std::any_of(
              unsafe_configuration_issues.begin(),
              unsafe_configuration_issues.end(),
              [](const ValidationIssue& issue) {
                  return issue.code == "unsafe-configuration-directory" &&
                         issue.fatal;
              }),
          "unsafe configuration directory was not reported fail-closed");

    std::vector<ValidationIssue> unsafe_log_issues;
    Check(!ParseRuntimeSettingsJson(
               R"json({"schemaVersion":1,"logFile":"C:/outside.log"})json",
               unsafe_log_issues, "runtime.json")
               .has_value(),
          "runtime settings accepted an absolute log path");
    Check(std::any_of(
              unsafe_log_issues.begin(), unsafe_log_issues.end(),
              [](const ValidationIssue& issue) {
                  return issue.code == "unsafe-log-path" && issue.fatal;
              }),
          "unsafe log path was not reported fail-closed");

    for (const auto* unsafe_path : {"NUL", "scripts/Axles.log:stream",
                                    "scripts/trailing. /Axles.log"}) {
        std::vector<ValidationIssue> windows_path_issues;
        const std::string settings =
            std::string("{\"schemaVersion\":2,\"logFile\":\"") +
            unsafe_path + "\"}";
        Check(!ParseRuntimeSettingsJson(settings, windows_path_issues,
                                        "runtime.json")
                   .has_value(),
              "runtime settings accepted an unsafe Windows path");
    }
}

void TestPortableRuntimeSettingsDocument() {
    RuntimeSettings settings;
    settings.enabled = false;
    settings.discovery_interval_ms = 400U;
    settings.recovery_interval_ms = 3200U;
    settings.restore_on_unload = false;
    settings.configuration_directory =
        "scripts\\ExampleTransitPack\\VehicleSettings";
    settings.log_file = "scripts\\ExampleTransitPack\\Axles.log";
    std::vector<ValidationIssue> issues;
    const auto document = SerializePortableRuntimeSettingsJson(
        settings, issues, "runtime.json");
    Check(document.has_value() && issues.empty(),
          "valid portable runtime settings were not serialized");
    Check(document->find("scripts/ExampleTransitPack/VehicleSettings") !=
              std::string::npos &&
              document->find("scripts/ExampleTransitPack/Axles.log") !=
                  std::string::npos &&
              document->find('\\') == std::string::npos,
          "serialized runtime paths were not portable");
    std::vector<ValidationIssue> round_trip_issues;
    const auto round_trip = ParseRuntimeSettingsJson(
        *document, round_trip_issues, "runtime.json");
    Check(round_trip.has_value() && round_trip_issues.empty() &&
              round_trip->schema_version == 2U && !round_trip->enabled &&
              round_trip->discovery_interval_ms == 400U &&
              round_trip->recovery_interval_ms == 3200U &&
              !round_trip->restore_on_unload,
          "portable settings round trip changed non-path values");

    std::vector<ValidationIssue> legacy_issues;
    const auto legacy = ParseRuntimeSettingsJson(
        R"json({"schemaVersion":1,"configurationDirectory":"configs","logFile":"logs/Axles.log"})json",
        legacy_issues, "legacy-runtime.json");
    Check(!legacy.has_value(),
          "schema-1 fixture unexpectedly accepted schema-2-only configurationDirectory");
    legacy_issues.clear();
    const auto valid_legacy = ParseRuntimeSettingsJson(
        R"json({"schemaVersion":1,"logFile":"logs/Axles.log"})json",
        legacy_issues, "legacy-runtime.json");
    Check(valid_legacy.has_value() && legacy_issues.empty(),
          "valid schema-1 settings fixture was rejected");
    const auto migrated = SerializePortableRuntimeSettingsJson(
        *valid_legacy, legacy_issues, "runtime.json");
    Check(migrated.has_value() &&
              migrated->find("VehicleWorkbenchAxles/configs") !=
                  std::string::npos &&
              migrated->find("VehicleWorkbenchAxles/logs/Axles.log") !=
                  std::string::npos,
          "schema-1 paths were not migrated without changing their base");

    RuntimeSettings unsafe;
    unsafe.configuration_directory = "../outside";
    std::vector<ValidationIssue> unsafe_issues;
    Check(!SerializePortableRuntimeSettingsJson(
               unsafe, unsafe_issues, "runtime.json")
               .has_value() &&
              std::any_of(
                  unsafe_issues.begin(), unsafe_issues.end(),
                  [](const ValidationIssue& issue) {
                      return issue.code == "unsafe-configuration-directory" &&
                             issue.fatal;
                  }),
          "portable settings serializer accepted parent traversal");

    RuntimeSettings future;
    future.schema_version = kRuntimeSettingsSchemaVersion + 1U;
    std::vector<ValidationIssue> future_issues;
    Check(!SerializePortableRuntimeSettingsJson(
               future, future_issues, "runtime.json")
               .has_value() &&
              !future_issues.empty(),
          "portable settings serializer downgraded an unknown schema");
}

void TestValidationAndParsing() {
    const std::string json_text = R"json({
      "schemaVersion": 1,
      "configurationId": "bus-steer-drive-rear-steer",
      "modelName": "example_bus",
      "modelHash": "0x12345678",
      "expectedWheelCount": 6,
      "minimumRuntimeVersion": "1.0.0",
      "wheelIndexMapping": {
        "source": "exported_vehicle_information",
        "reported_wheel_count": 6,
        "by_bone": {
          "wheel_lf": 5, "wheel_rf": 4,
          "wheel_lm1": 3, "wheel_rm1": 2,
          "wheel_lr": 1, "wheel_rr": 0
        }
      },
      "axles": [
        {"order":0,"role":"front","leftBone":"wheel_lf","rightBone":"wheel_rf","wheelIndices":[5,4],"steered":true,"powered":false},
        {"order":1,"role":"middle","leftBone":"wheel_lm1","rightBone":"wheel_rm1","wheelIndices":[3,2],"steered":false,"powered":true},
        {"order":2,"role":"rear","leftBone":"wheel_lr","rightBone":"wheel_rr","wheelIndices":[1,0],"steered":true,"powered":false}
      ],
      "compatibility": {"story-legacy":true,"story-enhanced":false}
    })json";
    std::vector<ValidationIssue> issues;
    auto parsed = ParseConfigurationJson(json_text, "1.0.0", issues, "bus.json");
    Check(parsed.has_value() && issues.empty(),
          "valid six-wheel JSON did not parse");
    Check(parsed->wheel_index_map.at("wheel_lf") == 5,
          "explicit target wheel mapping was rewritten");
    Check(parsed->story_legacy && !parsed->story_enhanced,
          "target-specific compatibility was not preserved");
    Check(!parsed->axles.front().steering_gain.has_value(),
          "legacy boolean-only configuration did not remain implicit");

    const std::string remapped_json = R"json({
      "schemaVersion": 1,
      "configurationId": "visual-flip-bus",
      "modelName": "visual_flip_bus",
      "modelHash": "0x5A17B055",
      "expectedWheelCount": 6,
      "minimumRuntimeVersion": "2.1.0",
      "wheelIndexMap": {
        "wheel_lf":0,"wheel_rf":1,"wheel_lm1":2,"wheel_rm1":3,
        "wheel_lr":4,"wheel_rr":5
      },
      "axles": [
        {"order":0,"role":"front","leftBone":"wheel_lm1","rightBone":"wheel_rm1","steered":true,"powered":false},
        {"order":1,"role":"middle","leftBone":"wheel_lf","rightBone":"wheel_rf","steered":false,"powered":true},
        {"order":2,"role":"rear","leftBone":"wheel_lr","rightBone":"wheel_rr","steered":true,"powered":false}
      ],
      "intentionalLayoutOverride": {
        "mode":"visual_instancing_remap",
        "physicalBonePairs":[["wheel_lm1","wheel_rm1"],["wheel_lf","wheel_rf"],["wheel_lr","wheel_rr"]],
        "bonePositionSha256":"0000000000000000000000000000000000000000000000000000000000000000",
        "reason":"Author-reviewed single/dual/single wheel-family layout"
      },
      "compatibility":{"story-legacy":true}
    })json";
    std::vector<ValidationIssue> old_remapped_issues;
    Check(!ParseConfigurationJson(
               remapped_json, "2.0.0", old_remapped_issues,
               "remapped-old-runtime.json").has_value(),
          "runtime 2.0.0 parsed a custom physical-order configuration");
    std::vector<ValidationIssue> remapped_issues;
    const auto parsed_remapped = ParseConfigurationJson(
        remapped_json, "2.1.0", remapped_issues, "remapped.json");
    Check(parsed_remapped.has_value() && remapped_issues.empty() &&
              parsed_remapped->intentional_layout_override.has_value(),
          "intentional physical-order override did not parse");

    auto missing_compatibility_json = json_text;
    const std::string compatibility_fixture =
        ",\n      \"compatibility\": {\"story-legacy\":true,\"story-enhanced\":false}";
    const auto compatibility_fixture_position =
        missing_compatibility_json.find(compatibility_fixture);
    Check(compatibility_fixture_position != std::string::npos,
          "compatibility parser fixture was not found");
    missing_compatibility_json.erase(compatibility_fixture_position,
                                     compatibility_fixture.size());
    std::vector<ValidationIssue> missing_compatibility_issues;
    Check(!ParseConfigurationJson(
               missing_compatibility_json, "1.0.0",
               missing_compatibility_issues, "missing-compatibility.json")
               .has_value(),
          "missing compatibility defaulted to a Story target");

    auto unknown_compatibility_json = json_text;
    unknown_compatibility_json.replace(
        unknown_compatibility_json.find("\"story-legacy\":true"),
        std::string("\"story-legacy\":true").size(),
        "\"story\":true");
    std::vector<ValidationIssue> unknown_compatibility_issues;
    Check(!ParseConfigurationJson(
               unknown_compatibility_json, "1.0.0",
               unknown_compatibility_issues, "unknown-compatibility.json")
               .has_value(),
          "unrecognized compatibility key enabled a Story target");

    auto legacy_signed_json = json_text;
    const std::string rear_legacy =
        "\"steered\":true,\"powered\":false}";
    const auto rear_position = legacy_signed_json.rfind(rear_legacy);
    Check(rear_position != std::string::npos,
          "signed gain parser fixture was not found");
    legacy_signed_json.replace(
        rear_position, rear_legacy.size(),
        "\"steered\":true,\"steeringGain\":-0.22,\"powered\":false}");
    std::vector<ValidationIssue> legacy_signed_issues;
    const auto legacy_signed = ParseConfigurationJson(
        legacy_signed_json, "2.0.0", legacy_signed_issues, "legacy-signed.json");
    Check(!legacy_signed.has_value(),
          "schema 1 accepted a non-legacy signed steering gain");

    auto signed_json = legacy_signed_json;
    signed_json.replace(signed_json.find("\"schemaVersion\": 1"), 18,
                        "\"schemaVersion\": 2");
    const std::string minimum_legacy =
        "\"minimumRuntimeVersion\": \"1.0.0\"";
    signed_json.replace(
        signed_json.find(minimum_legacy), minimum_legacy.size(),
        "\"minimumRuntimeVersion\": \"2.0.0\"");
    const std::string front_legacy =
        "\"steered\":true,\"powered\":false}";
    const auto front_position = signed_json.find(front_legacy);
    signed_json.replace(
        front_position, front_legacy.size(),
        "\"steered\":true,\"steeringGain\":1.0,\"powered\":false}");
    const std::string middle_legacy =
        "\"steered\":false,\"powered\":true}";
    const auto middle_position = signed_json.find(middle_legacy);
    signed_json.replace(
        middle_position, middle_legacy.size(),
        "\"steered\":false,\"steeringGain\":0.0,\"powered\":true}");
    const auto compatibility_position = signed_json.find(
        "\"compatibility\":");
    signed_json.insert(
        compatibility_position,
        "\"steeringCalculation\":{"
        "\"mode\":\"manual\",\"algorithmVersion\":1,"
        "\"bonePositionSha256\":\"" + std::string(64U, '0') + "\"},\n      ");
    std::vector<ValidationIssue> signed_issues;
    const auto parsed_signed = ParseConfigurationJson(
        signed_json, "2.0.0", signed_issues, "signed.json");
    Check(parsed_signed.has_value() && signed_issues.empty() &&
              parsed_signed->axles.back().steering_gain.has_value() &&
              std::abs(*parsed_signed->axles.back().steering_gain + 0.22) <
                  0.000001,
          "signed steeringGain did not parse exactly");

    auto automatic_json = signed_json;
    automatic_json.replace(
        automatic_json.find("\"mode\":\"manual\""),
        std::string("\"mode\":\"manual\"").size(),
        "\"mode\":\"automaticGeometry\"");
    const std::string evidence_end = "\"},\n      \"compatibility\"";
    const auto evidence_end_position = automatic_json.find(evidence_end);
    Check(evidence_end_position != std::string::npos,
          "automatic evidence parser fixture was not found");
    automatic_json.insert(
        evidence_end_position + 1U,
        ",\"runtimeRecompute\":true,"
        "\"referenceSelection\":\"farthest_steered_axle\","
        "\"pivotLongitudinalPosition\":-1.0,"
        "\"pivotSource\":\"selected_fixed_axles\","
        "\"pivotAxleOrders\":[1],\"referenceAxleOrder\":0,"
        "\"referenceLockDegrees\":35.0,"
        "\"pairPositionTolerance\":0.01,"
        "\"positionEpsilon\":0.0001");
    std::vector<ValidationIssue> old_automatic_issues;
    const auto old_automatic = ParseConfigurationJson(
        automatic_json, "4.1.0", old_automatic_issues,
        "automatic-old-runtime.json");
    Check(!old_automatic.has_value() && std::any_of(
              old_automatic_issues.begin(), old_automatic_issues.end(),
              [](const ValidationIssue& issue) {
                  return issue.code == "runtime-geometry-version-too-old";
              }),
          "runtime recompute accepted minimumRuntimeVersion 2.0.0");
    const std::string automatic_old_minimum =
        "\"minimumRuntimeVersion\": \"2.0.0\"";
    automatic_json.replace(
        automatic_json.find(automatic_old_minimum),
        automatic_old_minimum.size(),
        "\"minimumRuntimeVersion\": \"4.1.0\"");
    std::vector<ValidationIssue> automatic_issues;
    const auto parsed_automatic = ParseConfigurationJson(
        automatic_json, "4.1.0", automatic_issues, "automatic.json");
    Check(parsed_automatic.has_value() && automatic_issues.empty() &&
              parsed_automatic->steering_calculation.has_value() &&
              parsed_automatic->steering_calculation->runtime_recompute &&
              parsed_automatic->steering_calculation
                      ->reference_selection == "farthest_steered_axle" &&
              parsed_automatic->steering_calculation
                  ->pair_position_tolerance == 0.01 &&
              parsed_automatic->steering_calculation->position_epsilon ==
                  0.0001,
          "automatic reproducibility tolerances did not parse");

    auto signed_gain = *parsed;
    PromoteToSignedSchema(signed_gain);
    Check(!ValidateConfiguration(signed_gain, "2.0.0").empty(),
          "schema 2 accepted an entirely legacy steering configuration");
    signed_gain.axles[0].steering_gain = 1.0;
    signed_gain.axles[1].steering_gain = 0.0;
    signed_gain.axles[2].steering_gain = -0.22;
    Check(ValidateConfiguration(signed_gain, "2.0.0").empty(),
          "valid signed steering gain was rejected");
    auto nonsteered_gain = signed_gain;
    nonsteered_gain.axles[1].steering_gain = 0.25;
    Check(!ValidateConfiguration(nonsteered_gain, "2.0.0").empty(),
          "non-steered axle accepted non-zero steering gain");
    auto out_of_range_gain = signed_gain;
    out_of_range_gain.axles[2].steering_gain = -1.01;
    Check(!ValidateConfiguration(out_of_range_gain, "2.0.0").empty(),
          "out-of-range steering gain was accepted");

    auto automatic = signed_gain;
    automatic.steering_calculation->mode = "automaticGeometry";
    automatic.steering_calculation->pivot_longitudinal_position = -1.0;
    automatic.steering_calculation->pivot_source = "selected_fixed_axles";
    automatic.steering_calculation->pivot_axle_orders = {1};
    automatic.steering_calculation->reference_axle_order = 0;
    automatic.steering_calculation->reference_lock_degrees = 35.0;
    automatic.steering_calculation->pair_position_tolerance = 0.01;
    automatic.steering_calculation->position_epsilon = 0.0001;
    Check(ValidateConfiguration(automatic, "2.0.0").empty(),
          "complete automatic steering provenance was rejected");
    auto incomplete_automatic = automatic;
    incomplete_automatic.steering_calculation->position_epsilon.reset();
    Check(!ValidateConfiguration(incomplete_automatic, "2.0.0").empty(),
          "automatic provenance omitted a reproducibility input");
    auto invalid_manual = signed_gain;
    invalid_manual.steering_calculation->pair_position_tolerance = 0.01;
    Check(!ValidateConfiguration(invalid_manual, "2.0.0").empty(),
          "manual provenance accepted an automatic-only tolerance");
    auto invalid_manual_recompute = signed_gain;
    invalid_manual_recompute.steering_calculation->runtime_recompute = true;
    invalid_manual_recompute.steering_calculation->reference_selection =
        "farthest_steered_axle";
    Check(!ValidateConfiguration(invalid_manual_recompute, "2.0.0").empty(),
          "manual provenance accepted runtime geometry recompute");

    auto cosmetic = *parsed;
    cosmetic.wheel_index_map.emplace("wheel_dual_lf", 6);
    const auto cosmetic_issues = ValidateConfiguration(cosmetic, "1.0.0");
    Check(!cosmetic_issues.empty(),
          "cosmetic dual tyre was counted as a physical slot");

    auto too_many = MakeConfiguration(5);
    AxleDefinition sixth = too_many.axles.back();
    sixth.order = 5;
    too_many.axles.push_back(sixth);
    too_many.expected_wheel_count = 12;
    const auto too_many_issues = ValidateConfiguration(too_many, "1.0.0");
    Check(!too_many_issues.empty(),
          "vehicle beyond five physical pairs was accepted");

    std::vector<ValidationIssue> newer_issues;
    const auto newer = ParseConfigurationJson(
        std::string(json_text).replace(json_text.find("\"schemaVersion\": 1"),
                                       18, "\"schemaVersion\": 5"),
        "2.0.0", newer_issues, "future.json");
    Check(!newer.has_value(), "newer schema did not fail closed");

    const std::string schema_zero = R"json({
      "schemaVersion": 0,
      "configurationId": "legacy-four-wheel",
      "modelName": "legacy_fixture",
      "modelHash": "0x87654321",
      "expectedWheelCount": 4,
      "minimumRuntimeVersion": "1.0.0",
      "axles": [
        {"order":0,"role":"front","leftBone":"wheel_lf","rightBone":"wheel_rf","wheelIndices":[1,0],"steered":true,"powered":false},
        {"order":1,"role":"rear","leftBone":"wheel_lr","rightBone":"wheel_rr","wheelIndices":[3,2],"steered":false,"powered":true}
      ],
      "compatibility": {"storyLegacy": true}
    })json";
    std::vector<ValidationIssue> migration_issues;
    const auto migrated = ParseConfigurationJson(
        schema_zero, "1.0.0", migration_issues, "legacy.json");
    Check(migrated.has_value() && migrated->schema_version == 1,
          "schema-zero configuration was not migrated");
    Check(migrated->wheel_index_map.at("wheel_lf") == 1 &&
              !migration_issues.empty() && !migration_issues.front().fatal,
          "schema-zero wheel index migration was not reported");

    const auto temporary =
        std::filesystem::temp_directory_path() /
        ("vwa-config-test-" + std::to_string(
             std::chrono::high_resolution_clock::now()
                 .time_since_epoch()
                 .count()));
    std::filesystem::create_directories(temporary);
    {
        std::ofstream(temporary / "first.json") << json_text;
        std::ofstream(temporary / "second.json") << json_text;
        auto other = json_text;
        other.replace(other.find("0x12345678"), 10, "0x22345678");
        std::ofstream(temporary / "other.json") << other;
    }
    const auto catalog = LoadConfigurationDirectory(temporary, "1.0.0");
    std::filesystem::remove_all(temporary);
    Check(catalog.files_seen == 3 && catalog.active.size() == 1,
          "duplicate hashes did not isolate only conflicting configurations");
    Check(catalog.active.count(0x22345678U) == 1,
          "unrelated configuration was disabled with duplicate hash");
    for (const auto& issue : catalog.issues) {
        Check(issue.source_name.find('/') == std::string::npos &&
                  issue.source_name.find('\\') == std::string::npos,
              "configuration diagnostics leaked an absolute path");
    }
}

} // namespace

int main() {
    try {
        TestVariableLengthRuntime();
        TestLiveBoneFingerprintBlocksWrongOverride();
        TestLiveBoneFingerprintAcceptsCanonicalMapAndFailsClosedOnReadError();
        TestLiveWheelGenerationRebuildsBaseline();
        TestLiveWheelGenerationGuardsPlanningAndRestoration();
        TestIntentionalPhysicalOrderOverride();
        TestInvalidWheelCountAndRollback();
        TestRecoveryRetainsOriginalBaseline();
        TestShutdownRestorationRetries();
        TestShutdownReleasesObsoleteVehicleIdentities();
        TestOnlineAndUnsupportedGuards();
        TestSignedSteeringGainCapabilityAndRestore();
        TestVolatileSteeringMaintenanceIgnoresFixedWheels();
        TestRuntimeGeometryUsesAuthoritativePhysicalOrder();
        TestAxleSupportBiasApplyRollbackAndRestore();
        TestAxleSupportBiasParsingAndValidation();
        TestRuntimeSettingsContract();
        TestPortableRuntimeSettingsDocument();
        TestValidationAndParsing();
        std::cout << "VehicleWorkbenchAxles core tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "VehicleWorkbenchAxles core test failure: " << error.what()
                  << '\n';
        return EXIT_FAILURE;
    }
}
