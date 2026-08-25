#if !defined(_WIN32)
#error The ASI skeleton target is Windows-only
#endif

#include <windows.h>

#include <cstdint>

namespace {

#if defined(VWA_ASI_EDITION_Legacy)
constexpr const char* kTarget = "story-legacy";
#elif defined(VWA_ASI_EDITION_Enhanced)
constexpr const char* kTarget = "story-enhanced";
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
    1,
    VWA_RUNTIME_VERSION,
    kTarget,
    "implemented-awaiting-validated-profile",
};

} // namespace

// This export lets the Workbench inspect a built artifact without loading game
// accessors.  It is not a public mod/plugin ABI.
extern "C" __declspec(dllexport) const RuntimeDescriptor*
VehicleWorkbenchAxles_GetDescriptor() noexcept {
    return &kDescriptor;
}

// A deployment packager must refuse this artifact until a separately reviewed
// profile and ScriptHook host bridge replace this fail-closed gate.
extern "C" __declspec(dllexport) bool
VehicleWorkbenchAxles_HasValidatedProfile() noexcept {
    return false;
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
    }
    // DllMain deliberately performs no file I/O, thread creation, ScriptHook
    // registration, signature resolution, or memory access.
    return TRUE;
}
