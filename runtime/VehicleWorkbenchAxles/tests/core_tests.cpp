#include "vehicle_workbench_axles/configuration.hpp"
#include "vehicle_workbench_axles/runtime.hpp"
#include "vehicle_workbench_axles/wheel_access.hpp"

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
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

    bool bound{false};
    std::vector<std::uint16_t> flags{0, 0, 0, 0};
};

class Host final : public IVehicleHost {
public:
    GameIdentity DetectGame() const override { return game; }
    bool IsOnlineSession() const override { return online; }
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

    GameIdentity game{Edition::Legacy, 9999, "mock-fingerprint"};
    bool online{false};
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
        if (powered) {
            flags[index] = static_cast<std::uint16_t>(flags[index] | kDrivenBit);
        } else {
            flags[index] =
                static_cast<std::uint16_t>(flags[index] & ~kDrivenBit);
        }
        ++powered_writes;
        return true;
    }

    Edition target{Edition::Legacy};
    bool supported{true};
    bool resolved{false};
    std::uint32_t maximum_axles{5};
    std::string failure;
    std::vector<std::uint16_t> flags;
    std::optional<std::uint32_t> fail_next_write_at;
    std::size_t reads{0};
    std::size_t writes{0};
    std::size_t powered_writes{0};
    std::size_t resolve_calls{0};
};

const std::vector<std::pair<std::string, std::string>> kAllPairs{
    {"wheel_lf", "wheel_rf"},
    {"wheel_lm1", "wheel_rm1"},
    {"wheel_lm2", "wheel_rm2"},
    {"wheel_lm3", "wheel_rm3"},
    {"wheel_lr", "wheel_rr"},
};

AxleConfiguration MakeConfiguration(std::size_t axle_count,
                                    std::uint32_t model_hash = 0x12345678U) {
    AxleConfiguration result;
    result.configuration_id = "fixture-" + std::to_string(axle_count);
    result.model_name = "fixture";
    result.model_hash = model_hash;
    result.expected_wheel_count = static_cast<std::uint32_t>(axle_count * 2U);
    result.minimum_runtime_version = "1.0.0";

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
    profiled_adapter.Reset();
    Check(!profiled_adapter.IsResolved() && !profile->bound,
          "profile reset retained resolved accessors");
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
                                       18, "\"schemaVersion\": 2"),
        "1.0.0", newer_issues, "future.json");
    Check(!newer.has_value(), "newer schema did not fail closed");

    const std::string schema_zero = R"json({
      "schemaVersion": 0,
      "configurationId": "legacy-four-wheel",
      "modelName": "legacy_fixture",
      "modelHash": "0x87654321",
      "expectedWheelCount": 4,
      "axles": [
        {"order":0,"role":"front","leftBone":"wheel_lf","rightBone":"wheel_rf","wheelIndices":[1,0],"steered":true,"powered":false},
        {"order":1,"role":"rear","leftBone":"wheel_lr","rightBone":"wheel_rr","wheelIndices":[3,2],"steered":false,"powered":true}
      ]
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
        TestInvalidWheelCountAndRollback();
        TestOnlineAndUnsupportedGuards();
        TestValidationAndParsing();
        std::cout << "VehicleWorkbenchAxles core tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "VehicleWorkbenchAxles core test failure: " << error.what()
                  << '\n';
        return EXIT_FAILURE;
    }
}
