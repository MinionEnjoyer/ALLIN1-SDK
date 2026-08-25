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

private:
    struct State;
    std::unique_ptr<State> state_;
};

} // namespace vwa
