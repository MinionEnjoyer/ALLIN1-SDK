#pragma once

#include "vehicle_workbench_axles/types.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace vwa {

class ISignatureResolver {
public:
    virtual ~ISignatureResolver() = default;

    // Resolve is required to search the current module image.  It must not
    // accept absolute addresses from configuration data.
    virtual std::optional<std::uintptr_t>
    Resolve(const std::string& pattern, const std::string& mask) = 0;
    virtual bool IsExecutable(std::uintptr_t address,
                              std::size_t minimum_bytes) const = 0;
    virtual bool IsInGameModule(std::uintptr_t address,
                                std::size_t minimum_bytes) const = 0;
    virtual bool BytesMatch(std::uintptr_t address,
                            const std::vector<std::uint8_t>& expected) const = 0;
};

struct SignatureRequirement {
    std::string logical_name;
    std::string pattern;
    std::string mask;
    std::vector<std::uint8_t> expected_prefix;
    std::size_t minimum_executable_bytes{1};
};

// Build profiles are compiled adapter code, never vehicle JSON.  A profile
// reacquires and validates the host entity inside every operation; it must not
// retain a raw game vehicle or wheel-array pointer between calls.
class ICompiledWheelProfile {
public:
    virtual ~ICompiledWheelProfile() = default;
    virtual GameIdentity Identity() const = 0;
    // Exact identities remain the default contract for externally supplied
    // profiles. A compiled signature-gated profile may override this only when
    // every private layout value is derived and canary-checked at runtime.
    virtual bool MatchesIdentity(const GameIdentity& game) const {
        const auto identity = Identity();
        return identity.edition == game.edition && identity.build != 0 &&
               identity.build == game.build &&
               !identity.executable_fingerprint.empty() &&
               identity.executable_fingerprint == game.executable_fingerprint;
    }
    virtual std::uint32_t MaximumPhysicalAxles() const noexcept = 0;
    virtual std::vector<SignatureRequirement>
    SignatureRequirements() const = 0;
    virtual bool Bind(const std::vector<std::uintptr_t>& resolved_addresses,
                      std::string& failure) = 0;
    virtual void Unbind() noexcept = 0;

    virtual bool GetWheelCount(const VehicleSnapshot&, std::uint32_t&) = 0;
    virtual bool ReadWheelFlags(const VehicleSnapshot&, std::uint32_t,
                                std::uint16_t&) = 0;
    virtual bool WriteWheelFlags(const VehicleSnapshot&, std::uint32_t,
                                 std::uint16_t) = 0;
    virtual bool SetWheelPowered(const VehicleSnapshot&, std::uint32_t,
                                 bool) = 0;
    // Canonical CWheel bone IDs (11..20) let the runtime prove that an
    // explicitly overridden collection slot still refers to the intended
    // physical wheel before it performs any writes.
    virtual bool SupportsWheelBoneId() const noexcept { return false; }
    virtual bool ReadWheelBoneId(const VehicleSnapshot&, std::uint32_t,
                                 std::int32_t&) { return false; }
    // A live generation token fingerprints the current CVehicle wheel-array
    // storage without exposing or retaining its pointers.  It lets the core
    // distinguish a rebuilt wheel collection from a recycled handle/model
    // pair before restoring an older baseline.
    virtual bool SupportsWheelGenerationToken() const noexcept { return false; }
    virtual bool ReadWheelGenerationToken(const VehicleSnapshot&,
                                          std::uint64_t&) { return false; }
    // Signed per-wheel steering gain is an optional, build-profile capability.
    // Existing reviewed profiles remain source/binary compatible at the
    // contract level and explicitly report no support until all three methods
    // are implemented and acceptance-tested for an exact game build.
    virtual bool SupportsSteeringGain() const noexcept { return false; }
    virtual bool ReadWheelSteeringGain(const VehicleSnapshot&, std::uint32_t,
                                       double&) { return false; }
    virtual bool WriteWheelSteeringGain(const VehicleSnapshot&, std::uint32_t,
                                        double) { return false; }
    // Vehicle-local wheel positions are an optional exact-build capability.
    // Automatic geometry recomputation is fail-closed when a configuration
    // requests it and the active profile cannot provide authoritative data.
    virtual bool SupportsWheelLocalPosition() const noexcept { return false; }
    virtual bool ReadWheelLocalPosition(const VehicleSnapshot&, std::uint32_t,
                                        WheelLocalPosition&) { return false; }
    // Per-wheel suspension StaticForce is an optional exact-build capability.
    // Profiles must expose read and write together after their offsets and
    // semantics have been reviewed for that executable identity.
    virtual bool SupportsStaticForce() const noexcept { return false; }
    virtual bool ReadWheelStaticForce(const VehicleSnapshot&, std::uint32_t,
                                      double&) { return false; }
    virtual bool WriteWheelStaticForce(const VehicleSnapshot&, std::uint32_t,
                                       double) { return false; }
};

class IWheelAccess {
public:
    virtual ~IWheelAccess() = default;

    virtual bool Resolve(const GameIdentity& game,
                         ISignatureResolver& resolver) = 0;
    virtual bool IsSupportedBuild(const GameIdentity& game) const = 0;
    virtual bool IsResolved() const noexcept = 0;
    virtual Edition TargetEdition() const noexcept = 0;
    virtual std::uint32_t MaximumPhysicalAxles() const noexcept = 0;
    virtual const std::string& LastFailure() const noexcept = 0;
    virtual void Reset() noexcept = 0;

    virtual bool GetWheelCount(const VehicleSnapshot& vehicle,
                               std::uint32_t& count) = 0;
    virtual bool ReadWheelFlags(const VehicleSnapshot& vehicle,
                                std::uint32_t index,
                                std::uint16_t& flags) = 0;
    virtual bool WriteWheelFlags(const VehicleSnapshot& vehicle,
                                 std::uint32_t index,
                                 std::uint16_t flags) = 0;
    virtual bool SetWheelPowered(const VehicleSnapshot& vehicle,
                                 std::uint32_t index,
                                 bool powered) = 0;
    virtual bool SupportsWheelBoneId() const noexcept { return false; }
    virtual bool ReadWheelBoneId(const VehicleSnapshot&, std::uint32_t,
                                 std::int32_t&) { return false; }
    virtual bool SupportsWheelGenerationToken() const noexcept {
        return false;
    }
    virtual bool ReadWheelGenerationToken(const VehicleSnapshot&,
                                          std::uint64_t&) { return false; }
    virtual bool SupportsSteeringGain() const noexcept = 0;
    virtual bool ReadWheelSteeringGain(const VehicleSnapshot& vehicle,
                                       std::uint32_t index,
                                       double& gain) = 0;
    virtual bool WriteWheelSteeringGain(const VehicleSnapshot& vehicle,
                                        std::uint32_t index,
                                        double gain) = 0;
    virtual bool SupportsWheelLocalPosition() const noexcept { return false; }
    virtual bool ReadWheelLocalPosition(const VehicleSnapshot&,
                                        std::uint32_t,
                                        WheelLocalPosition&) { return false; }
    virtual bool SupportsStaticForce() const noexcept = 0;
    virtual bool ReadWheelStaticForce(const VehicleSnapshot& vehicle,
                                      std::uint32_t index,
                                      double& force) = 0;
    virtual bool WriteWheelStaticForce(const VehicleSnapshot& vehicle,
                                       std::uint32_t index,
                                       double force) = 0;
};

class LegacyWheelAccess final : public IWheelAccess {
public:
    LegacyWheelAccess();
    explicit LegacyWheelAccess(
        std::vector<std::shared_ptr<ICompiledWheelProfile>> profiles);
    ~LegacyWheelAccess() override;
    LegacyWheelAccess(const LegacyWheelAccess&) = delete;
    LegacyWheelAccess& operator=(const LegacyWheelAccess&) = delete;

    bool Resolve(const GameIdentity&, ISignatureResolver&) override;
    bool IsSupportedBuild(const GameIdentity&) const override;
    bool IsResolved() const noexcept override;
    Edition TargetEdition() const noexcept override;
    std::uint32_t MaximumPhysicalAxles() const noexcept override;
    const std::string& LastFailure() const noexcept override;
    void Reset() noexcept override;
    bool GetWheelCount(const VehicleSnapshot&, std::uint32_t&) override;
    bool ReadWheelFlags(const VehicleSnapshot&, std::uint32_t,
                        std::uint16_t&) override;
    bool WriteWheelFlags(const VehicleSnapshot&, std::uint32_t,
                         std::uint16_t) override;
    bool SetWheelPowered(const VehicleSnapshot&, std::uint32_t, bool) override;
    bool SupportsWheelBoneId() const noexcept override;
    bool ReadWheelBoneId(const VehicleSnapshot&, std::uint32_t,
                         std::int32_t&) override;
    bool SupportsWheelGenerationToken() const noexcept override;
    bool ReadWheelGenerationToken(const VehicleSnapshot&,
                                  std::uint64_t&) override;
    bool SupportsSteeringGain() const noexcept override;
    bool ReadWheelSteeringGain(const VehicleSnapshot&, std::uint32_t,
                               double&) override;
    bool WriteWheelSteeringGain(const VehicleSnapshot&, std::uint32_t,
                                double) override;
    bool SupportsWheelLocalPosition() const noexcept override;
    bool ReadWheelLocalPosition(const VehicleSnapshot&, std::uint32_t,
                                WheelLocalPosition&) override;
    bool SupportsStaticForce() const noexcept override;
    bool ReadWheelStaticForce(const VehicleSnapshot&, std::uint32_t,
                              double&) override;
    bool WriteWheelStaticForce(const VehicleSnapshot&, std::uint32_t,
                               double) override;

private:
    struct State;
    std::unique_ptr<State> state_;
};

class EnhancedWheelAccess final : public IWheelAccess {
public:
    EnhancedWheelAccess();
    explicit EnhancedWheelAccess(
        std::vector<std::shared_ptr<ICompiledWheelProfile>> profiles);
    ~EnhancedWheelAccess() override;
    EnhancedWheelAccess(const EnhancedWheelAccess&) = delete;
    EnhancedWheelAccess& operator=(const EnhancedWheelAccess&) = delete;

    bool Resolve(const GameIdentity&, ISignatureResolver&) override;
    bool IsSupportedBuild(const GameIdentity&) const override;
    bool IsResolved() const noexcept override;
    Edition TargetEdition() const noexcept override;
    std::uint32_t MaximumPhysicalAxles() const noexcept override;
    const std::string& LastFailure() const noexcept override;
    void Reset() noexcept override;
    bool GetWheelCount(const VehicleSnapshot&, std::uint32_t&) override;
    bool ReadWheelFlags(const VehicleSnapshot&, std::uint32_t,
                        std::uint16_t&) override;
    bool WriteWheelFlags(const VehicleSnapshot&, std::uint32_t,
                         std::uint16_t) override;
    bool SetWheelPowered(const VehicleSnapshot&, std::uint32_t, bool) override;
    bool SupportsWheelBoneId() const noexcept override;
    bool ReadWheelBoneId(const VehicleSnapshot&, std::uint32_t,
                         std::int32_t&) override;
    bool SupportsWheelGenerationToken() const noexcept override;
    bool ReadWheelGenerationToken(const VehicleSnapshot&,
                                  std::uint64_t&) override;
    bool SupportsSteeringGain() const noexcept override;
    bool ReadWheelSteeringGain(const VehicleSnapshot&, std::uint32_t,
                               double&) override;
    bool WriteWheelSteeringGain(const VehicleSnapshot&, std::uint32_t,
                                double) override;
    bool SupportsWheelLocalPosition() const noexcept override;
    bool ReadWheelLocalPosition(const VehicleSnapshot&, std::uint32_t,
                                WheelLocalPosition&) override;
    bool SupportsStaticForce() const noexcept override;
    bool ReadWheelStaticForce(const VehicleSnapshot&, std::uint32_t,
                              double&) override;
    bool WriteWheelStaticForce(const VehicleSnapshot&, std::uint32_t,
                               double) override;

private:
    struct State;
    std::unique_ptr<State> state_;
};

} // namespace vwa
