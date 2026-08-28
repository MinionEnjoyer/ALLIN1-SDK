#if !defined(_WIN32)
#error The Story Mode ASI host target is Windows-only
#endif

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <exception>
#include <string>
#include <utility>
#include <vector>

#include "vehicle_workbench_axles/configuration.hpp"
#include "vehicle_workbench_axles/runtime.hpp"
#include "vehicle_workbench_axles/wheel_access.hpp"

#include "story_host_bridge.hpp"

namespace {

#if defined(VWA_ASI_EDITION_Legacy)
constexpr const char* kTarget = "story-legacy";
constexpr const char* kBuildTargetMarker =
    "VehicleWorkbenchAxles.BuildTarget=story-legacy";
#elif defined(VWA_ASI_EDITION_Enhanced)
constexpr const char* kTarget = "story-enhanced";
constexpr const char* kBuildTargetMarker =
    "VehicleWorkbenchAxles.BuildTarget=story-enhanced";
#else
#error An explicit Story Mode edition is required
#endif

struct RuntimeDescriptor {
    std::uint32_t abi_version;
    std::uint32_t maximum_schema_version;
    const char* runtime_version;
    const char* target;
    const char* support_status;
};

const RuntimeDescriptor kDescriptor{
    1,
    vwa::kAxleSchemaVersion,
    VWA_RUNTIME_VERSION,
    kTarget,
    "script-hook-host-ready-signature-gated-wheel-profile",
};

HMODULE g_module = nullptr;
vwa::story::ScriptHookApi g_script_hook;
std::atomic_bool g_stop_requested{false};
std::atomic_bool g_registered{false};

constexpr vwa::Edition CompiledEdition() noexcept {
#if defined(VWA_ASI_EDITION_Legacy)
    return vwa::Edition::Legacy;
#else
    return vwa::Edition::Enhanced;
#endif
}

void ScriptMainBody() {
    const auto paths = vwa::story::ResolveRuntimePaths(g_module);
    if (!paths.has_value()) {
        OutputDebugStringA(
            "VehicleWorkbenchAxles: ASI data path could not be resolved\n");
        return;
    }

    std::vector<vwa::ValidationIssue> settings_issues;
    auto settings =
        vwa::story::LoadRuntimeSettings(*paths, settings_issues);
    bool used_fallback_log = false;
    const auto log_path =
        vwa::story::ResolveLogPath(*paths, settings, used_fallback_log);
    vwa::story::FileLogSink log(log_path);
    if (!log.IsOpen()) {
        OutputDebugStringA(
            "VehicleWorkbenchAxles: runtime log could not be opened\n");
        return;
    }
    log.Write(vwa::LogLevel::Info, "host-start",
              std::string("VehicleWorkbenchAxles ") + VWA_RUNTIME_VERSION +
                  " native ScriptHookV host started for " + kTarget);
    if (used_fallback_log) {
        log.Write(vwa::LogLevel::Warning, "unsafe-log-path-rejected",
                  "The configured log path was not a safe relative path; "
                  "the default runtime log is in use");
    }
    bool used_fallback_configuration = false;
    const auto configuration_path = vwa::story::ResolveConfigurationPath(
        *paths, settings, used_fallback_configuration);
    if (used_fallback_configuration) {
        log.Write(vwa::LogLevel::Warning,
                  "unsafe-configuration-directory-rejected",
                  "The configured configuration directory was not a safe "
                  "relative path; the default configuration directory is in "
                  "use");
    }
    for (const auto& issue : settings_issues) {
        log.Write(issue.fatal ? vwa::LogLevel::Warning : vwa::LogLevel::Info,
                  issue.code, issue.message);
    }
    if (std::any_of(settings_issues.begin(), settings_issues.end(),
                    [](const vwa::ValidationIssue& issue) {
                        return issue.fatal;
                    })) {
        log.Write(vwa::LogLevel::Warning, "runtime-settings-invalid",
                  "VehicleWorkbenchAxles stopped before configuration, "
                  "profile, vehicle, or wheel work because runtime.json is "
                  "invalid");
        return;
    }
    if (!settings.enabled) {
        log.Write(vwa::LogLevel::Info, "controller-disabled",
                  "VehicleWorkbenchAxles is disabled by runtime.json; no "
                  "configuration, profile, vehicle, or wheel work was started");
        return;
    }

    while (!g_stop_requested.load(std::memory_order_acquire) &&
           !g_script_hook.CanInvokeNatives()) {
        g_script_hook.Wait(0);
    }
    if (g_stop_requested.load(std::memory_order_acquire)) return;

    vwa::story::StoryVehicleHost host(g_script_hook, CompiledEdition(), log);
    const auto identity = host.DetectGame();
    log.Write(vwa::LogLevel::Info, "game-identity",
              std::string("Detected ") + vwa::ToString(identity.edition) +
                  " build " + std::to_string(identity.build) +
                  (identity.executable_fingerprint.empty()
                       ? " without an executable fingerprint"
                       : " with an executable SHA-256 fingerprint"));

#if defined(VWA_ASI_EDITION_Legacy)
    vwa::LegacyWheelAccess wheel_access;
#else
    vwa::EnhancedWheelAccess wheel_access;
#endif
    vwa::story::ExecutableSignatureResolver resolver;
    auto catalog = vwa::LoadConfigurationDirectory(
        configuration_path, VWA_RUNTIME_VERSION);
    log.Write(vwa::LogLevel::Info, "configuration-discovery",
              "Inspected configured relative directory '" +
                  settings.configuration_directory + "': " +
                  std::to_string(catalog.files_seen) +
                  " configuration file(s); " +
                  std::to_string(catalog.active.size()) +
                  " non-conflicting model configuration(s) parsed");

    vwa::AxleRuntime runtime(host, wheel_access, log, settings);
    const bool started = runtime.Start(std::move(catalog), resolver);
    if (!started) {
        log.Write(vwa::LogLevel::Warning, "runtime-inactive",
                  "The native host is healthy, but axle control remains "
                  "inactive. Review the preceding fail-closed profile or "
                  "configuration diagnostic");
    } else {
        log.Write(vwa::LogLevel::Info, "wheel-profile-active",
                  std::string("Activated the ") + kTarget +
                      " signature-gated wheel profile with canonical bone-ID "
                      "verification, selective steering/drive flags, signed "
                      "steering gain, StaticForce, and physics activation");
    }

    while (!g_stop_requested.load(std::memory_order_acquire)) {
        // Service is intentionally called every ScriptHook frame. The shared
        // runtime performs its own bounded discovery/recovery scheduling, but
        // its online-session guard is therefore evaluated without a 100 ms or
        // multi-second host delay.
        runtime.Service(std::chrono::steady_clock::now());
        g_script_hook.Wait(0);
    }
    runtime.Shutdown();
    log.Write(vwa::LogLevel::Info, "host-stop",
              "Native ScriptHookV host stopped");
}

void ScriptMain() noexcept {
    try {
        ScriptMainBody();
    } catch (const std::exception&) {
        // Never allow a C++ exception to cross ScriptHook's callback boundary.
        // Detailed parser/runtime failures normally reach the file log before
        // this final containment boundary.
        OutputDebugStringA(
            "VehicleWorkbenchAxles: unhandled host exception; runtime stopped\n");
    } catch (...) {
        OutputDebugStringA(
            "VehicleWorkbenchAxles: unknown host exception; runtime stopped\n");
    }
}

} // namespace

// This export lets the Workbench inspect a built artifact without loading game
// accessors.  It is not a public mod/plugin ABI.
extern "C" __declspec(dllexport) const RuntimeDescriptor*
VehicleWorkbenchAxles_GetDescriptor() noexcept {
    return &kDescriptor;
}

// This reports that the binary contains the compiled, signature-gated wheel
// profile. Distribution eligibility remains a separate receipt/build gate in
// the SDK and is deliberately not implied by this export.
extern "C" __declspec(dllexport) bool
VehicleWorkbenchAxles_HasValidatedProfile() noexcept {
    return true;
}

// Distinguishes a complete ScriptHook lifecycle bridge from the former DLL
// shape-only skeleton. This does not imply that a wheel-memory profile has
// been validated; the separate profile export above remains authoritative.
extern "C" __declspec(dllexport) bool
VehicleWorkbenchAxles_HasScriptHookHost() noexcept {
    return true;
}

// A unique, non-executing marker lets the SDK verify that a staged binary was
// compiled for the requested edition. Both edition names also occur in shared
// enum/string tables, so searching for the bare target name is ambiguous.
extern "C" __declspec(dllexport) const char*
VehicleWorkbenchAxles_GetBuildTargetMarker() noexcept {
    return kBuildTargetMarker;
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        g_module = module;
        g_stop_requested.store(false, std::memory_order_release);
        if (!g_script_hook.Bind() ||
            !g_script_hook.Register(module, &ScriptMain)) {
            OutputDebugStringA(
                "VehicleWorkbenchAxles: ScriptHookV host registration failed\n");
            // Returning FALSE makes the ASI loader record a deterministic
            // module-load failure instead of accepting a dead controller that
            // can never create its normal file log.
            return FALSE;
        }
        g_registered.store(true, std::memory_order_release);
    } else if (reason == DLL_PROCESS_DETACH) {
        g_stop_requested.store(true, std::memory_order_release);
        if (g_registered.exchange(false, std::memory_order_acq_rel)) {
            g_script_hook.Unregister(module);
        }
    }
    // DllMain performs only ScriptHook registration/unregistration. File I/O,
    // native calls, configuration parsing, and profile resolution occur on
    // ScriptHook's cooperative script thread.
    return TRUE;
}
