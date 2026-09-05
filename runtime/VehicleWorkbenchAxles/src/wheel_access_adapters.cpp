#include "vehicle_workbench_axles/wheel_access.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#endif

namespace vwa {

// The shared runtime never contains permanent CVehicle/CWheel offsets. These
// compiled profiles derive the small wheel-layout contract from executable
// signatures independently for Legacy and Enhanced. Entity addresses are
// reacquired from ScriptHookV for every operation; no game pointer survives a
// call boundary.

struct LegacyWheelAccess::State {
    explicit State(std::vector<std::shared_ptr<ICompiledWheelProfile>> items)
        : profiles(std::move(items)) {}
    std::vector<std::shared_ptr<ICompiledWheelProfile>> profiles;
    std::shared_ptr<ICompiledWheelProfile> active;
    std::uint32_t maximum_physical_axles{0};
    std::string failure{
        "No validated Legacy wheel-access profiles are installed"};
};

struct EnhancedWheelAccess::State {
    explicit State(std::vector<std::shared_ptr<ICompiledWheelProfile>> items)
        : profiles(std::move(items)) {}
    std::vector<std::shared_ptr<ICompiledWheelProfile>> profiles;
    std::shared_ptr<ICompiledWheelProfile> active;
    std::uint32_t maximum_physical_axles{0};
    std::string failure{
        "No validated Enhanced wheel-access profiles are installed"};
};

namespace {

#if defined(_WIN32)

bool IsReadableProtection(DWORD protection) noexcept {
    if ((protection & (PAGE_GUARD | PAGE_NOACCESS)) != 0) return false;
    switch (protection & 0xFFU) {
    case PAGE_READONLY:
    case PAGE_READWRITE:
    case PAGE_WRITECOPY:
    case PAGE_EXECUTE_READ:
    case PAGE_EXECUTE_READWRITE:
    case PAGE_EXECUTE_WRITECOPY:
        return true;
    default:
        return false;
    }
}

bool IsWritableProtection(DWORD protection) noexcept {
    if ((protection & (PAGE_GUARD | PAGE_NOACCESS)) != 0) return false;
    switch (protection & 0xFFU) {
    case PAGE_READWRITE:
    case PAGE_WRITECOPY:
    case PAGE_EXECUTE_READWRITE:
    case PAGE_EXECUTE_WRITECOPY:
        return true;
    default:
        return false;
    }
}

bool CanAccess(std::uintptr_t address, std::size_t size,
               bool write) noexcept {
    if (address < 0x10000U || size == 0 ||
        address > std::numeric_limits<std::uintptr_t>::max() - size) {
        return false;
    }
    const auto end = address + size;
    auto cursor = address;
    while (cursor < end) {
        MEMORY_BASIC_INFORMATION information{};
        if (VirtualQuery(reinterpret_cast<const void*>(cursor), &information,
                         sizeof(information)) != sizeof(information) ||
            information.State != MEM_COMMIT ||
            !(write ? IsWritableProtection(information.Protect)
                     : IsReadableProtection(information.Protect))) {
            return false;
        }
        const auto region_begin = reinterpret_cast<std::uintptr_t>(
            information.BaseAddress);
        if (region_begin >
            std::numeric_limits<std::uintptr_t>::max() -
                information.RegionSize) {
            return false;
        }
        const auto region_end = region_begin + information.RegionSize;
        if (region_end <= cursor) return false;
        cursor = std::min(end, region_end);
    }
    return true;
}

template <typename T>
bool ReadMemory(std::uintptr_t address, T& value) noexcept {
    if (!CanAccess(address, sizeof(T), false)) return false;
    std::memcpy(&value, reinterpret_cast<const void*>(address), sizeof(T));
    return true;
}

template <typename T>
bool WriteMemory(std::uintptr_t address, const T& value) noexcept {
    if (!CanAccess(address, sizeof(T), true)) return false;
    std::memcpy(reinterpret_cast<void*>(address), &value, sizeof(T));
    return true;
}

SignatureRequirement MakeRequirement(
    std::string logical_name, std::string pattern,
    std::vector<std::uint8_t> expected_prefix,
    std::size_t minimum_executable_bytes = 32U) {
    std::istringstream tokens(pattern);
    std::string token;
    std::string mask;
    while (tokens >> token) {
        mask.push_back(token == "?" || token == "??" ? '?' : 'x');
    }
    return {std::move(logical_name), std::move(pattern), std::move(mask),
            std::move(expected_prefix), minimum_executable_bytes};
}

class SignatureWheelProfile final : public ICompiledWheelProfile {
public:
    explicit SignatureWheelProfile(Edition edition) : edition_(edition) {}

    GameIdentity Identity() const override {
        return {edition_, 0, "signature-gated-wheel-layout-v1"};
    }

    bool MatchesIdentity(const GameIdentity& game) const override {
        return game.edition == edition_ && game.build != 0 &&
               game.executable_fingerprint.size() == 64U;
    }

    std::uint32_t MaximumPhysicalAxles() const noexcept override { return 5; }

    std::vector<SignatureRequirement> SignatureRequirements() const override {
        if (edition_ == Edition::Enhanced) {
            return {
                MakeRequirement("enhanced-wheel-count-layout",
                                "4c 8b 89 ? ? ? ? 45 31 d2 0f 1f",
                                {0x4c, 0x8b, 0x89}),
                MakeRequirement("enhanced-wheel-steering-layout",
                                "f3 0f 59 04 88 f3 0f 59 86",
                                {0xf3, 0x0f, 0x59, 0x04, 0x88, 0xf3, 0x0f,
                                 0x59, 0x86}),
                MakeRequirement("enhanced-wheel-id-layout",
                                "4b 8b 04 d1 0f bf 88",
                                {0x4b, 0x8b, 0x04, 0xd1, 0x0f, 0xbf, 0x88}),
                MakeRequirement(
                    "enhanced-wheel-flags-layout",
                    "48 8b 86 ? ? ? ? 48 8b 04 d8 8b 80 ? ? ? ? a8 ? 75 ? 40 f6 c5",
                    {0x48, 0x8b, 0x86}),
            };
        }
        return {
            MakeRequirement(
                "legacy-wheel-count-layout",
                "48 63 99 ? ? ? ? 45 33 c0 45 8b d0 48 85 db",
                {0x48, 0x63, 0x99}),
            MakeRequirement(
                "legacy-wheel-steering-layout",
                "f3 0f 59 05 ? ? ? ? f3 0f 59 83 ? ? ? ? f3 0f 10 c8 0f c6 c9 00",
                {0xf3, 0x0f, 0x59, 0x05}),
            MakeRequirement("legacy-wheel-id-layout",
                            "0f bf 88 ? ? ? ? 3b ca 74 17",
                            {0x0f, 0xbf, 0x88}),
            MakeRequirement("legacy-wheel-flags-layout",
                            "eb 02 33 c9 f6 81 ? ? ? ? 01 75 43",
                            {0xeb, 0x02, 0x33, 0xc9, 0xf6, 0x81}),
        };
    }

    bool Bind(const std::vector<std::uintptr_t>& addresses,
              std::string& failure) override {
        Unbind();
        if (addresses.size() != 4U) {
            failure = "Wheel profile did not resolve all four layout signatures";
            return false;
        }
        std::int32_t wheel_count = 0;
        std::int32_t selector = 0;
        std::int32_t wheel_id = 0;
        std::int32_t dynamic_flags = 0;
        const bool read = edition_ == Edition::Enhanced
            ? ReadMemory(addresses[0] - 9U, wheel_count) &&
                  ReadMemory(addresses[1] + 9U, selector) &&
                  ReadMemory(addresses[2] + 7U, wheel_id) &&
                  ReadMemory(addresses[3] + 13U, dynamic_flags)
            : ReadMemory(addresses[0] + 3U, wheel_count) &&
                  ReadMemory(addresses[1] + 12U, selector) &&
                  ReadMemory(addresses[2] + 3U, wheel_id) &&
                  ReadMemory(addresses[3] + 6U, dynamic_flags);
        if (!read) {
            failure = "Wheel profile could not read its derived layout values";
            return false;
        }
        if (wheel_count < 0x100 || wheel_count > 0x4000 ||
            selector < 0x20 || selector > 0x1000 ||
            wheel_id < 0 || wheel_id > 0x400 ||
            dynamic_flags < 0 || dynamic_flags > 0x400) {
            failure = "Wheel profile derived implausible private layout values";
            return false;
        }
        const auto script_hook = GetModuleHandleW(L"ScriptHookV.dll");
        if (!script_hook) {
            failure = "ScriptHookV.dll is unavailable during wheel-profile bind";
            return false;
        }
        handle_to_address_ = reinterpret_cast<HandleToAddress>(GetProcAddress(
            script_hook, "?getScriptHandleBaseAddress@@YAPEAEH@Z"));
        if (!handle_to_address_) {
            failure = "ScriptHookV lacks getScriptHandleBaseAddress";
            return false;
        }
        wheel_count_offset_ = static_cast<std::uint32_t>(wheel_count);
        wheel_array_offset_ = wheel_count_offset_ - 8U;
        wheel_bone_map_offset_ = wheel_count_offset_ + 4U;
        steering_gain_offset_ = static_cast<std::uint32_t>(selector);
        static_force_offset_ = steering_gain_offset_ - 4U;
        wheel_id_offset_ = static_cast<std::uint32_t>(wheel_id);
        dynamic_flags_offset_ = static_cast<std::uint32_t>(dynamic_flags);
        bound_ = true;
        failure.clear();
        return true;
    }

    void Unbind() noexcept override {
        bound_ = false;
        handle_to_address_ = nullptr;
        wheel_count_offset_ = 0;
        wheel_array_offset_ = 0;
        wheel_bone_map_offset_ = 0;
        steering_gain_offset_ = 0;
        static_force_offset_ = 0;
        wheel_id_offset_ = 0;
        dynamic_flags_offset_ = 0;
    }

    bool GetWheelCount(const VehicleSnapshot& vehicle,
                       std::uint32_t& count) override {
        std::uintptr_t ignored = 0;
        return ResolveLayout(vehicle, count, ignored);
    }

    bool ReadWheelFlags(const VehicleSnapshot& vehicle, std::uint32_t index,
                        std::uint16_t& flags) override {
        std::uintptr_t wheel = 0;
        if (!ResolveWheel(vehicle, index, wheel)) return false;
        std::uint32_t raw = 0;
        if (!ReadMemory(wheel + dynamic_flags_offset_ + 4U, raw)) return false;
        flags = static_cast<std::uint16_t>(raw & 0xffffU);
        return true;
    }

    bool WriteWheelFlags(const VehicleSnapshot& vehicle, std::uint32_t index,
                         std::uint16_t flags) override {
        std::uintptr_t wheel = 0;
        if (!ResolveWheel(vehicle, index, wheel)) return false;
        const auto address = wheel + dynamic_flags_offset_ + 4U;
        std::uint32_t raw = 0;
        if (!ReadMemory(address, raw)) return false;
        constexpr std::uint32_t managed = 0x18U;
        const auto desired =
            (raw & ~managed) | (static_cast<std::uint32_t>(flags) & managed);
        return desired == raw || WriteMemory(address, desired);
    }

    bool SetWheelPowered(const VehicleSnapshot& vehicle, std::uint32_t index,
                         bool powered) override {
        std::uintptr_t wheel = 0;
        if (!ResolveWheel(vehicle, index, wheel)) return false;
        const auto address = wheel + dynamic_flags_offset_ + 4U;
        std::uint32_t raw = 0;
        if (!ReadMemory(address, raw)) return false;
        const auto desired = powered ? (raw | 0x10U) : (raw & ~0x10U);
        return desired == raw || WriteMemory(address, desired);
    }

    bool SupportsWheelBoneId() const noexcept override { return bound_; }

    bool ReadWheelBoneId(const VehicleSnapshot& vehicle, std::uint32_t index,
                         std::int32_t& bone_id) override {
        std::uintptr_t wheel = 0;
        return ResolveWheel(vehicle, index, wheel, &bone_id);
    }

    bool SupportsWheelGenerationToken() const noexcept override {
        return bound_;
    }

    bool ReadWheelGenerationToken(const VehicleSnapshot& vehicle,
                                  std::uint64_t& generation) override {
        generation = 0;
        std::uintptr_t vehicle_address = 0;
        std::uint32_t count = 0;
        std::uintptr_t wheel_array = 0;
        if (!ResolveLayoutHeader(vehicle, vehicle_address, count,
                                 wheel_array)) {
            return false;
        }

        // FNV-1a is used only as a process-local identity fingerprint. Raw
        // addresses never leave this method and are reacquired on every call.
        std::uint64_t hash = 1469598103934665603ULL;
        MixGeneration(hash, vehicle.entity_id);
        MixGeneration(hash, vehicle.model_hash);
        MixGeneration(hash, count);
        MixGeneration(hash, static_cast<std::uint64_t>(wheel_array));
        for (std::uint32_t index = 0; index < count; ++index) {
            std::uintptr_t wheel = 0;
            std::int32_t bone_id = 0;
            if (!ResolveIndexedWheel(vehicle_address, wheel_array, count,
                                     index, wheel, bone_id)) {
                return false;
            }
            MixGeneration(hash, static_cast<std::uint64_t>(wheel));
            MixGeneration(hash, static_cast<std::uint32_t>(bone_id));
        }
        generation = hash == 0 ? 1 : hash;
        return true;
    }

    bool SupportsSteeringGain() const noexcept override { return bound_; }

    bool ReadWheelSteeringGain(const VehicleSnapshot& vehicle,
                               std::uint32_t index, double& gain) override {
        std::uintptr_t wheel = 0;
        float value = 0.0F;
        if (!ResolveWheel(vehicle, index, wheel) ||
            !ReadMemory(wheel + steering_gain_offset_, value) ||
            !std::isfinite(value)) {
            return false;
        }
        gain = value;
        return true;
    }

    bool WriteWheelSteeringGain(const VehicleSnapshot& vehicle,
                                std::uint32_t index, double gain) override {
        if (!std::isfinite(gain) || gain < -4.0 || gain > 4.0) return false;
        std::uintptr_t wheel = 0;
        if (!ResolveWheel(vehicle, index, wheel)) return false;
        const auto value = static_cast<float>(gain);
        return WriteMemory(wheel + steering_gain_offset_, value);
    }

    bool SupportsStaticForce() const noexcept override { return bound_; }

    bool ReadWheelStaticForce(const VehicleSnapshot& vehicle,
                              std::uint32_t index, double& force) override {
        std::uintptr_t wheel = 0;
        float value = 0.0F;
        if (!ResolveWheel(vehicle, index, wheel) ||
            !ReadMemory(wheel + static_force_offset_, value) ||
            !std::isfinite(value)) {
            return false;
        }
        force = value;
        return true;
    }

    bool WriteWheelStaticForce(const VehicleSnapshot& vehicle,
                               std::uint32_t index, double force) override {
        if (!std::isfinite(force) || std::abs(force) > 1.0e9) return false;
        std::uintptr_t wheel = 0;
        if (!ResolveWheel(vehicle, index, wheel)) return false;
        const auto value = static_cast<float>(force);
        return WriteMemory(wheel + static_force_offset_, value);
    }

private:
    using HandleToAddress = std::uint8_t*(__cdecl*)(std::int32_t);

    static void MixGeneration(std::uint64_t& hash,
                              std::uint64_t value) noexcept {
        for (unsigned shift = 0; shift < 64U; shift += 8U) {
            hash ^= (value >> shift) & 0xffU;
            hash *= 1099511628211ULL;
        }
    }

    static bool IsCanonicalBoneId(std::int32_t bone_id) noexcept {
        return bone_id >= 11 && bone_id <= 20;
    }

    bool ResolveVehicle(const VehicleSnapshot& vehicle,
                        std::uintptr_t& address) const noexcept {
        address = 0;
        if (!bound_ || !handle_to_address_ || vehicle.entity_id == 0 ||
            vehicle.entity_id > static_cast<std::uint64_t>(
                                    std::numeric_limits<std::int32_t>::max())) {
            return false;
        }
        address = reinterpret_cast<std::uintptr_t>(handle_to_address_(
            static_cast<std::int32_t>(vehicle.entity_id)));
        // The exact fields are validated by ReadMemory in
        // ResolveLayoutHeader. Avoid duplicate VirtualQuery calls here.
        const auto maximum_header_offset = std::max(
            {wheel_array_offset_, wheel_count_offset_,
             wheel_bone_map_offset_ + 9U});
        return address >= 0x10000U &&
               address <= std::numeric_limits<std::uintptr_t>::max() -
                              maximum_header_offset;
    }

    bool ResolveLayoutHeader(const VehicleSnapshot& vehicle,
                             std::uintptr_t& vehicle_address,
                             std::uint32_t& count,
                             std::uintptr_t& wheel_array) const noexcept {
        count = 0;
        wheel_array = 0;
        vehicle_address = 0;
        std::int32_t signed_count = 0;
        if (!ResolveVehicle(vehicle, vehicle_address) ||
            !ReadMemory(vehicle_address + wheel_count_offset_, signed_count) ||
            signed_count < 2 || signed_count > 10 ||
            (signed_count & 1) != 0 ||
            !ReadMemory(vehicle_address + wheel_array_offset_, wheel_array) ||
            !CanAccess(wheel_array,
                       static_cast<std::size_t>(signed_count) *
                           sizeof(std::uintptr_t),
                       false)) {
            return false;
        }
        count = static_cast<std::uint32_t>(signed_count);
        return true;
    }

    bool ResolveIndexedWheel(std::uintptr_t vehicle_address,
                             std::uintptr_t wheel_array,
                             std::uint32_t count, std::uint32_t index,
                             std::uintptr_t& wheel,
                             std::int32_t& bone_id) const noexcept {
        wheel = 0;
        bone_id = 0;
        if (index >= count ||
            !ReadMemory(wheel_array +
                            static_cast<std::uintptr_t>(index) *
                                sizeof(std::uintptr_t),
                        wheel) ||
            wheel < 0x10000U) {
            return false;
        }
        const auto maximum_wheel_offset = std::max(
            {wheel_id_offset_ +
                 static_cast<std::uint32_t>(sizeof(std::int32_t)),
             dynamic_flags_offset_ + 4U +
                 static_cast<std::uint32_t>(sizeof(std::uint32_t)),
             steering_gain_offset_ +
                 static_cast<std::uint32_t>(sizeof(float)),
             static_force_offset_ +
                 static_cast<std::uint32_t>(sizeof(float))});
        if (wheel > std::numeric_limits<std::uintptr_t>::max() -
                        maximum_wheel_offset ||
            !ReadMemory(wheel + wheel_id_offset_, bone_id) ||
            !IsCanonicalBoneId(bone_id)) {
            return false;
        }
        std::uint8_t mapped_index = 0xffU;
        return ReadMemory(vehicle_address + wheel_bone_map_offset_ +
                              static_cast<std::uintptr_t>(bone_id - 11),
                          mapped_index) &&
               mapped_index == static_cast<std::uint8_t>(index);
    }

    bool ResolveLayout(const VehicleSnapshot& vehicle, std::uint32_t& count,
                       std::uintptr_t& wheel_array) const noexcept {
        std::uintptr_t vehicle_address = 0;
        if (!ResolveLayoutHeader(vehicle, vehicle_address, count,
                                 wheel_array)) {
            return false;
        }
        std::uint16_t seen_bones = 0;
        for (std::uint32_t index = 0; index < count; ++index) {
            std::uintptr_t wheel = 0;
            std::int32_t bone_id = 0;
            if (!ResolveIndexedWheel(vehicle_address, wheel_array, count,
                                     index, wheel, bone_id)) {
                return false;
            }
            const auto bit = static_cast<std::uint16_t>(1U << (bone_id - 11));
            if ((seen_bones & bit) != 0) return false;
            seen_bones = static_cast<std::uint16_t>(seen_bones | bit);
        }
        return true;
    }

    bool ResolveWheel(const VehicleSnapshot& vehicle, std::uint32_t index,
                      std::uintptr_t& wheel,
                      std::int32_t* resolved_bone_id = nullptr) const noexcept {
        wheel = 0;
        std::uintptr_t vehicle_address = 0;
        std::uint32_t count = 0;
        std::uintptr_t wheel_array = 0;
        if (!ResolveLayoutHeader(vehicle, vehicle_address, count,
                                 wheel_array)) {
            return false;
        }
        std::int32_t bone_id = 0;
        if (!ResolveIndexedWheel(vehicle_address, wheel_array, count, index,
                                 wheel, bone_id)) {
            return false;
        }
        if (resolved_bone_id) *resolved_bone_id = bone_id;
        // Callers immediately validate the exact field they read or write.
        // Do not VirtualQuery the entire wheel object on every accessor call.
        return true;
    }

    Edition edition_{Edition::Unknown};
    HandleToAddress handle_to_address_{nullptr};
    bool bound_{false};
    std::uint32_t wheel_count_offset_{0};
    std::uint32_t wheel_array_offset_{0};
    std::uint32_t wheel_bone_map_offset_{0};
    std::uint32_t steering_gain_offset_{0};
    std::uint32_t static_force_offset_{0};
    std::uint32_t wheel_id_offset_{0};
    std::uint32_t dynamic_flags_offset_{0};
};

std::vector<std::shared_ptr<ICompiledWheelProfile>>
DefaultProfiles(Edition edition) {
    return {std::make_shared<SignatureWheelProfile>(edition)};
}

#else

std::vector<std::shared_ptr<ICompiledWheelProfile>>
DefaultProfiles(Edition) {
    return {};
}

#endif

template <typename State>
void ResetState(State& state, Edition edition) noexcept {
    if (state.active) state.active->Unbind();
    state.active.reset();
    state.maximum_physical_axles = 0;
    state.failure = edition == Edition::Legacy
                        ? "Legacy wheel-access adapter is not resolved"
                        : "Enhanced wheel-access adapter is not resolved";
}

template <typename State>
bool Supports(const State& state, Edition edition, const GameIdentity& game) {
    if (game.edition != edition) return false;
    return std::any_of(
        state.profiles.begin(), state.profiles.end(),
        [&](const auto& profile) {
            return profile && profile->MatchesIdentity(game);
        });
}

template <typename State>
bool ResolveState(State& state, Edition edition, const GameIdentity& game,
                  ISignatureResolver& resolver) {
    ResetState(state, edition);
    if (game.edition != edition) {
        state.failure =
            "Wheel-access adapter does not match the detected edition";
        return false;
    }
    if (game.build == 0) {
        state.failure = "Game build could not be detected exactly";
        return false;
    }
    if (game.executable_fingerprint.empty()) {
        state.failure = "Executable fingerprint is unavailable";
        return false;
    }

    const auto match = std::find_if(
        state.profiles.begin(), state.profiles.end(),
        [&](const auto& profile) {
            return profile && profile->MatchesIdentity(game);
        });
    if (match == state.profiles.end()) {
        state.failure = edition == Edition::Legacy
                            ? "No validated Legacy wheel-access profile matches "
                              "this exact build"
                            : "No validated Enhanced wheel-access profile matches "
                              "this exact build";
        return false;
    }

    const auto requirements = (*match)->SignatureRequirements();
    if (requirements.empty()) {
        state.failure =
            "Build profile contains no validated signature requirements";
        return false;
    }
    std::vector<std::uintptr_t> resolved;
    resolved.reserve(requirements.size());
    for (const auto& requirement : requirements) {
        if (requirement.logical_name.empty() || requirement.pattern.empty() ||
            requirement.mask.empty() || requirement.expected_prefix.empty() ||
            requirement.minimum_executable_bytes == 0) {
            state.failure = "Build profile has an incomplete signature contract";
            return false;
        }
        const auto address =
            resolver.Resolve(requirement.pattern, requirement.mask);
        if (!address.has_value() ||
            !resolver.IsExecutable(*address,
                                   requirement.minimum_executable_bytes) ||
            !resolver.IsInGameModule(*address,
                                     requirement.minimum_executable_bytes) ||
            !resolver.BytesMatch(*address, requirement.expected_prefix)) {
            state.failure = "Signature validation failed for '" +
                            requirement.logical_name + "'";
            return false;
        }
        resolved.push_back(*address);
    }

    const auto maximum_axles = (*match)->MaximumPhysicalAxles();
    if (maximum_axles < 2 || maximum_axles > 5) {
        state.failure =
            "Build profile declares an invalid physical axle capability";
        return false;
    }
    std::string bind_failure;
    if (!(*match)->Bind(resolved, bind_failure)) {
        state.failure = bind_failure.empty()
                            ? "Build profile rejected its resolved accessors"
                            : bind_failure;
        (*match)->Unbind();
        return false;
    }
    state.active = *match;
    state.maximum_physical_axles = maximum_axles;
    state.failure.clear();
    return true;
}

template <typename State>
bool GetCount(State& state, const VehicleSnapshot& vehicle,
              std::uint32_t& count) {
    return state.active && state.active->GetWheelCount(vehicle, count);
}

template <typename State>
bool ReadFlags(State& state, const VehicleSnapshot& vehicle,
               std::uint32_t index, std::uint16_t& flags) {
    return state.active &&
           state.active->ReadWheelFlags(vehicle, index, flags);
}

template <typename State>
bool WriteFlags(State& state, const VehicleSnapshot& vehicle,
                std::uint32_t index, std::uint16_t flags) {
    return state.active &&
           state.active->WriteWheelFlags(vehicle, index, flags);
}

template <typename State>
bool SetPowered(State& state, const VehicleSnapshot& vehicle,
                std::uint32_t index, bool powered) {
    return state.active &&
           state.active->SetWheelPowered(vehicle, index, powered);
}

template <typename State>
bool SupportsSteeringGain(const State& state) noexcept {
    return state.active && state.active->SupportsSteeringGain();
}

template <typename State>
bool ReadSteeringGain(State& state, const VehicleSnapshot& vehicle,
                      std::uint32_t index, double& gain) {
    return SupportsSteeringGain(state) &&
           state.active->ReadWheelSteeringGain(vehicle, index, gain);
}

template <typename State>
bool WriteSteeringGain(State& state, const VehicleSnapshot& vehicle,
                       std::uint32_t index, double gain) {
    return SupportsSteeringGain(state) &&
           state.active->WriteWheelSteeringGain(vehicle, index, gain);
}

template <typename State>
bool SupportsWheelBoneId(const State& state) noexcept {
    return state.active && state.active->SupportsWheelBoneId();
}

template <typename State>
bool ReadWheelBoneId(State& state, const VehicleSnapshot& vehicle,
                     std::uint32_t index, std::int32_t& bone_id) {
    return SupportsWheelBoneId(state) &&
           state.active->ReadWheelBoneId(vehicle, index, bone_id);
}

template <typename State>
bool SupportsWheelGenerationToken(const State& state) noexcept {
    return state.active && state.active->SupportsWheelGenerationToken();
}

template <typename State>
bool ReadWheelGenerationToken(State& state, const VehicleSnapshot& vehicle,
                              std::uint64_t& generation) {
    return SupportsWheelGenerationToken(state) &&
           state.active->ReadWheelGenerationToken(vehicle, generation);
}

template <typename State>
bool SupportsWheelLocalPosition(const State& state) noexcept {
    return state.active && state.active->SupportsWheelLocalPosition();
}

template <typename State>
bool ReadWheelLocalPosition(State& state, const VehicleSnapshot& vehicle,
                            std::uint32_t index,
                            WheelLocalPosition& position) {
    return SupportsWheelLocalPosition(state) &&
           state.active->ReadWheelLocalPosition(vehicle, index, position);
}

template <typename State>
bool SupportsStaticForce(const State& state) noexcept {
    return state.active && state.active->SupportsStaticForce();
}

template <typename State>
bool ReadStaticForce(State& state, const VehicleSnapshot& vehicle,
                     std::uint32_t index, double& force) {
    return SupportsStaticForce(state) &&
           state.active->ReadWheelStaticForce(vehicle, index, force);
}

template <typename State>
bool WriteStaticForce(State& state, const VehicleSnapshot& vehicle,
                      std::uint32_t index, double force) {
    return SupportsStaticForce(state) &&
           state.active->WriteWheelStaticForce(vehicle, index, force);
}

} // namespace

LegacyWheelAccess::LegacyWheelAccess()
    : LegacyWheelAccess(DefaultProfiles(Edition::Legacy)) {}
LegacyWheelAccess::LegacyWheelAccess(
    std::vector<std::shared_ptr<ICompiledWheelProfile>> profiles)
    : state_(std::make_unique<State>(std::move(profiles))) {}
LegacyWheelAccess::~LegacyWheelAccess() = default;

bool LegacyWheelAccess::Resolve(const GameIdentity& game,
                                ISignatureResolver& resolver) {
    return ResolveState(*state_, Edition::Legacy, game, resolver);
}
bool LegacyWheelAccess::IsSupportedBuild(const GameIdentity& game) const {
    return Supports(*state_, Edition::Legacy, game);
}
bool LegacyWheelAccess::IsResolved() const noexcept {
    return state_->active != nullptr;
}
Edition LegacyWheelAccess::TargetEdition() const noexcept {
    return Edition::Legacy;
}
std::uint32_t LegacyWheelAccess::MaximumPhysicalAxles() const noexcept {
    return state_->maximum_physical_axles;
}
const std::string& LegacyWheelAccess::LastFailure() const noexcept {
    return state_->failure;
}
void LegacyWheelAccess::Reset() noexcept {
    ResetState(*state_, Edition::Legacy);
}
bool LegacyWheelAccess::GetWheelCount(const VehicleSnapshot& vehicle,
                                      std::uint32_t& count) {
    return GetCount(*state_, vehicle, count);
}
bool LegacyWheelAccess::ReadWheelFlags(const VehicleSnapshot& vehicle,
                                       std::uint32_t index,
                                       std::uint16_t& flags) {
    return ReadFlags(*state_, vehicle, index, flags);
}
bool LegacyWheelAccess::WriteWheelFlags(const VehicleSnapshot& vehicle,
                                        std::uint32_t index,
                                        std::uint16_t flags) {
    return WriteFlags(*state_, vehicle, index, flags);
}
bool LegacyWheelAccess::SetWheelPowered(const VehicleSnapshot& vehicle,
                                        std::uint32_t index, bool powered) {
    return SetPowered(*state_, vehicle, index, powered);
}
bool LegacyWheelAccess::SupportsWheelBoneId() const noexcept {
    return vwa::SupportsWheelBoneId(*state_);
}
bool LegacyWheelAccess::ReadWheelBoneId(const VehicleSnapshot& vehicle,
                                        std::uint32_t index,
                                        std::int32_t& bone_id) {
    return vwa::ReadWheelBoneId(*state_, vehicle, index, bone_id);
}
bool LegacyWheelAccess::SupportsWheelGenerationToken() const noexcept {
    return vwa::SupportsWheelGenerationToken(*state_);
}
bool LegacyWheelAccess::ReadWheelGenerationToken(
    const VehicleSnapshot& vehicle, std::uint64_t& generation) {
    return vwa::ReadWheelGenerationToken(*state_, vehicle, generation);
}
bool LegacyWheelAccess::SupportsSteeringGain() const noexcept {
    return vwa::SupportsSteeringGain(*state_);
}
bool LegacyWheelAccess::ReadWheelSteeringGain(
    const VehicleSnapshot& vehicle, std::uint32_t index, double& gain) {
    return ReadSteeringGain(*state_, vehicle, index, gain);
}
bool LegacyWheelAccess::WriteWheelSteeringGain(
    const VehicleSnapshot& vehicle, std::uint32_t index, double gain) {
    return WriteSteeringGain(*state_, vehicle, index, gain);
}
bool LegacyWheelAccess::SupportsWheelLocalPosition() const noexcept {
    return vwa::SupportsWheelLocalPosition(*state_);
}
bool LegacyWheelAccess::ReadWheelLocalPosition(
    const VehicleSnapshot& vehicle, std::uint32_t index,
    WheelLocalPosition& position) {
    return vwa::ReadWheelLocalPosition(*state_, vehicle, index, position);
}
bool LegacyWheelAccess::SupportsStaticForce() const noexcept {
    return vwa::SupportsStaticForce(*state_);
}
bool LegacyWheelAccess::ReadWheelStaticForce(
    const VehicleSnapshot& vehicle, std::uint32_t index, double& force) {
    return ReadStaticForce(*state_, vehicle, index, force);
}
bool LegacyWheelAccess::WriteWheelStaticForce(
    const VehicleSnapshot& vehicle, std::uint32_t index, double force) {
    return WriteStaticForce(*state_, vehicle, index, force);
}

EnhancedWheelAccess::EnhancedWheelAccess()
    : EnhancedWheelAccess(DefaultProfiles(Edition::Enhanced)) {}
EnhancedWheelAccess::EnhancedWheelAccess(
    std::vector<std::shared_ptr<ICompiledWheelProfile>> profiles)
    : state_(std::make_unique<State>(std::move(profiles))) {}
EnhancedWheelAccess::~EnhancedWheelAccess() = default;

bool EnhancedWheelAccess::Resolve(const GameIdentity& game,
                                  ISignatureResolver& resolver) {
    return ResolveState(*state_, Edition::Enhanced, game, resolver);
}
bool EnhancedWheelAccess::IsSupportedBuild(const GameIdentity& game) const {
    return Supports(*state_, Edition::Enhanced, game);
}
bool EnhancedWheelAccess::IsResolved() const noexcept {
    return state_->active != nullptr;
}
Edition EnhancedWheelAccess::TargetEdition() const noexcept {
    return Edition::Enhanced;
}
std::uint32_t EnhancedWheelAccess::MaximumPhysicalAxles() const noexcept {
    return state_->maximum_physical_axles;
}
const std::string& EnhancedWheelAccess::LastFailure() const noexcept {
    return state_->failure;
}
void EnhancedWheelAccess::Reset() noexcept {
    ResetState(*state_, Edition::Enhanced);
}
bool EnhancedWheelAccess::GetWheelCount(const VehicleSnapshot& vehicle,
                                        std::uint32_t& count) {
    return GetCount(*state_, vehicle, count);
}
bool EnhancedWheelAccess::ReadWheelFlags(const VehicleSnapshot& vehicle,
                                         std::uint32_t index,
                                         std::uint16_t& flags) {
    return ReadFlags(*state_, vehicle, index, flags);
}
bool EnhancedWheelAccess::WriteWheelFlags(const VehicleSnapshot& vehicle,
                                          std::uint32_t index,
                                          std::uint16_t flags) {
    return WriteFlags(*state_, vehicle, index, flags);
}
bool EnhancedWheelAccess::SetWheelPowered(const VehicleSnapshot& vehicle,
                                          std::uint32_t index, bool powered) {
    return SetPowered(*state_, vehicle, index, powered);
}
bool EnhancedWheelAccess::SupportsWheelBoneId() const noexcept {
    return vwa::SupportsWheelBoneId(*state_);
}
bool EnhancedWheelAccess::ReadWheelBoneId(const VehicleSnapshot& vehicle,
                                          std::uint32_t index,
                                          std::int32_t& bone_id) {
    return vwa::ReadWheelBoneId(*state_, vehicle, index, bone_id);
}
bool EnhancedWheelAccess::SupportsWheelGenerationToken() const noexcept {
    return vwa::SupportsWheelGenerationToken(*state_);
}
bool EnhancedWheelAccess::ReadWheelGenerationToken(
    const VehicleSnapshot& vehicle, std::uint64_t& generation) {
    return vwa::ReadWheelGenerationToken(*state_, vehicle, generation);
}
bool EnhancedWheelAccess::SupportsSteeringGain() const noexcept {
    return vwa::SupportsSteeringGain(*state_);
}
bool EnhancedWheelAccess::ReadWheelSteeringGain(
    const VehicleSnapshot& vehicle, std::uint32_t index, double& gain) {
    return ReadSteeringGain(*state_, vehicle, index, gain);
}
bool EnhancedWheelAccess::WriteWheelSteeringGain(
    const VehicleSnapshot& vehicle, std::uint32_t index, double gain) {
    return WriteSteeringGain(*state_, vehicle, index, gain);
}
bool EnhancedWheelAccess::SupportsWheelLocalPosition() const noexcept {
    return vwa::SupportsWheelLocalPosition(*state_);
}
bool EnhancedWheelAccess::ReadWheelLocalPosition(
    const VehicleSnapshot& vehicle, std::uint32_t index,
    WheelLocalPosition& position) {
    return vwa::ReadWheelLocalPosition(*state_, vehicle, index, position);
}
bool EnhancedWheelAccess::SupportsStaticForce() const noexcept {
    return vwa::SupportsStaticForce(*state_);
}
bool EnhancedWheelAccess::ReadWheelStaticForce(
    const VehicleSnapshot& vehicle, std::uint32_t index, double& force) {
    return ReadStaticForce(*state_, vehicle, index, force);
}
bool EnhancedWheelAccess::WriteWheelStaticForce(
    const VehicleSnapshot& vehicle, std::uint32_t index, double force) {
    return WriteStaticForce(*state_, vehicle, index, force);
}

} // namespace vwa
