#pragma once

#include "vehicle_workbench_axles/configuration.hpp"
#include "vehicle_workbench_axles/wheel_access.hpp"

#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace vwa {

class IVehicleHost {
public:
    virtual ~IVehicleHost() = default;
    virtual GameIdentity DetectGame() const = 0;
    virtual bool IsOnlineSession() const = 0;
    virtual std::vector<VehicleSnapshot> EnumerateVehicles() = 0;
    virtual std::optional<VehicleSnapshot>
    LookupVehicle(std::uint64_t entity_id) = 0;
    // Suspension writes are never assumed safe merely because wheel access is
    // available. Hosts opt in only after an exact-build physics activation
    // path has been validated.
    virtual bool SupportsPhysicsActivation() const noexcept { return false; }
    virtual bool ActivatePhysics(const VehicleSnapshot&) { return false; }
};

enum class RuntimeState {
    Stopped,
    Starting,
    Running,
    DisabledOnline,
    UnsupportedBuild,
    NoValidConfigurations,
    Faulted,
};

enum class VehicleEvent {
    Created,
    OwnershipChanged,
    Repaired,
    WheelStateRecreated,
};

class AxleRuntime final {
public:
    AxleRuntime(IVehicleHost& host, IWheelAccess& wheel_access,
                ILogSink& log_sink, RuntimeSettings settings = {});
    ~AxleRuntime();

    AxleRuntime(const AxleRuntime&) = delete;
    AxleRuntime& operator=(const AxleRuntime&) = delete;

    bool Start(ConfigurationCatalog catalog,
               ISignatureResolver& signature_resolver);
    void Service(std::chrono::steady_clock::time_point now);
    void OnVehicleEvent(const VehicleSnapshot& vehicle, VehicleEvent event);
    void Shutdown();

    RuntimeState State() const noexcept;
    std::size_t TrackedVehicleCount() const noexcept;
    std::size_t ActiveConfigurationCount() const noexcept;

private:
    struct Implementation;
    std::unique_ptr<Implementation> implementation_;
};

} // namespace vwa
