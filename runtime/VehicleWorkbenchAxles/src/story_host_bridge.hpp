#pragma once

#if !defined(_WIN32)
#error The Story Mode host bridge is Windows-only
#endif

#include <windows.h>

#include "vehicle_workbench_axles/runtime.hpp"

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <optional>
#include <string>
#include <vector>

namespace vwa::story {

using ScriptMainCallback = void (*)();

// ScriptHookV is an external prerequisite and is never redistributed with the
// runtime. The bridge resolves its documented SDK exports from the module that
// loaded the ASI, avoiding a private import library or a copied SDK binary.
class ScriptHookApi final {
public:
    bool Bind() noexcept;
    bool Register(HMODULE module, ScriptMainCallback callback) const noexcept;
    void Unregister(HMODULE module) const noexcept;
    void Wait(DWORD milliseconds) const noexcept;

    bool ReadyForScript() const noexcept;
    bool CanInvokeNatives() const noexcept;
    std::optional<bool> InvokeBool(std::uint64_t hash) const noexcept;
    std::optional<std::uint32_t>
    InvokeEntityHash(std::uint64_t hash, std::int32_t entity) const noexcept;
    std::optional<bool>
    InvokeEntityBool(std::uint64_t hash, std::int32_t entity) const noexcept;
    bool InvokeEntityVoid(std::uint64_t hash,
                          std::int32_t entity) const noexcept;
    std::vector<std::int32_t> EnumerateVehicleHandles() const;
    const char* LastFailure() const noexcept;

private:
    using ScriptRegister = void(__cdecl*)(HMODULE, ScriptMainCallback);
    using ScriptUnregister = void(__cdecl*)(HMODULE);
    using ScriptWait = void(__cdecl*)(DWORD);
    using NativeInit = void(__cdecl*)(std::uint64_t);
    using NativePush64 = void(__cdecl*)(std::uint64_t);
    using NativeCall = std::uint64_t*(__cdecl*)();
    using NativeCanExecute = bool(__cdecl*)();
    using WorldGetAllVehicles = int(__cdecl*)(std::int32_t*, int);

    HMODULE script_hook_{nullptr};
    ScriptRegister script_register_{nullptr};
    ScriptUnregister script_unregister_{nullptr};
    ScriptWait script_wait_{nullptr};
    NativeInit native_init_{nullptr};
    NativePush64 native_push64_{nullptr};
    NativeCall native_call_{nullptr};
    NativeCanExecute native_can_execute_{nullptr};
    WorldGetAllVehicles world_get_all_vehicles_{nullptr};
    const char* failure_{"ScriptHookV host API is not bound"};
};

struct RuntimePaths {
    std::filesystem::path module_directory;
    std::filesystem::path data_directory;
    std::filesystem::path settings_file;
    std::filesystem::path configuration_directory;
    std::filesystem::path log_file;
};

std::optional<RuntimePaths> ResolveRuntimePaths(HMODULE module);
RuntimeSettings LoadRuntimeSettings(const RuntimePaths& paths,
                                    std::vector<ValidationIssue>& issues);
std::filesystem::path ResolveLogPath(const RuntimePaths& paths,
                                     const RuntimeSettings& settings,
                                     bool& used_fallback) noexcept;
std::filesystem::path ResolveConfigurationPath(
    const RuntimePaths& paths, const RuntimeSettings& settings,
    bool& used_fallback) noexcept;

class FileLogSink final : public ILogSink {
public:
    explicit FileLogSink(std::filesystem::path path);
    void Write(LogLevel level, const std::string& code,
               const std::string& message) override;
    bool IsOpen() const noexcept;

private:
    std::ofstream stream_;
};

class ExecutableSignatureResolver final : public ISignatureResolver {
public:
    ExecutableSignatureResolver();

    std::optional<std::uintptr_t>
    Resolve(const std::string& pattern, const std::string& mask) override;
    bool IsExecutable(std::uintptr_t address,
                      std::size_t minimum_bytes) const override;
    bool IsInGameModule(std::uintptr_t address,
                        std::size_t minimum_bytes) const override;
    bool BytesMatch(std::uintptr_t address,
                    const std::vector<std::uint8_t>& expected) const override;

private:
    struct ExecutableRange {
        std::uintptr_t begin{0};
        std::uintptr_t end{0};
    };

    std::uintptr_t module_begin_{0};
    std::uintptr_t module_end_{0};
    std::vector<ExecutableRange> executable_ranges_;
};

class StoryVehicleHost final : public IVehicleHost {
public:
    StoryVehicleHost(const ScriptHookApi& api, Edition compiled_edition,
                     ILogSink& log);

    GameIdentity DetectGame() const override;
    bool IsOnlineSession() const override;
    std::vector<VehicleSnapshot> EnumerateVehicles() override;
    std::optional<VehicleSnapshot>
    LookupVehicle(std::uint64_t entity_id) override;
    bool SupportsPhysicsActivation() const noexcept override;
    bool ActivatePhysics(const VehicleSnapshot& vehicle) override;

private:
    std::optional<VehicleSnapshot>
    Snapshot(std::int32_t handle) const;

    const ScriptHookApi& api_;
    Edition compiled_edition_{Edition::Unknown};
    ILogSink& log_;
    GameIdentity identity_;
    mutable bool native_failure_logged_{false};
};

} // namespace vwa::story
