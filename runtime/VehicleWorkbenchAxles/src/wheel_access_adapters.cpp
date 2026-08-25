#include "vehicle_workbench_axles/wheel_access.hpp"

#include <algorithm>
#include <memory>
#include <utility>
#include <vector>

namespace vwa {

// Intentionally no signature or layout constants are present in this file.
// Future edition modules supply reviewed ICompiledWheelProfile instances.  The
// default constructors receive an empty profile set and therefore fail before
// invoking ISignatureResolver or touching game memory.

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

bool ExactIdentityMatch(const GameIdentity& left,
                        const GameIdentity& right) noexcept {
    return left.edition == right.edition && left.build != 0 &&
           left.build == right.build && !left.executable_fingerprint.empty() &&
           left.executable_fingerprint == right.executable_fingerprint;
}

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
            return profile && ExactIdentityMatch(profile->Identity(), game);
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
            return profile && ExactIdentityMatch(profile->Identity(), game);
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

} // namespace

LegacyWheelAccess::LegacyWheelAccess()
    : LegacyWheelAccess(
          std::vector<std::shared_ptr<ICompiledWheelProfile>>{}) {}
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

EnhancedWheelAccess::EnhancedWheelAccess()
    : EnhancedWheelAccess(
          std::vector<std::shared_ptr<ICompiledWheelProfile>>{}) {}
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

} // namespace vwa
