#include "story_host_bridge.hpp"

#include <bcrypt.h>
#include <winver.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstring>
#include <iomanip>
#include <iterator>
#include <limits>
#include <sstream>
#include <system_error>

namespace vwa::story {
namespace {

constexpr std::size_t kMaximumRuntimeJsonBytes = 1024U * 1024U;
constexpr int kMaximumEnumeratedVehicles = 2048;

// Stable GTA V native identifiers, invoked through ScriptHookV. They are API
// identifiers, not executable offsets or memory-layout assumptions.
constexpr std::uint64_t kNetworkIsSessionActive = 0xD83C2B94E7508980ULL;
constexpr std::uint64_t kNetworkIsInTransition = 0x68049AEFF83D8F0AULL;
constexpr std::uint64_t kDoesEntityExist = 0x7239B21A38F536BAULL;
constexpr std::uint64_t kGetEntityModel = 0x9F47B058362C84B5ULL;
constexpr std::uint64_t kActivatePhysics = 0x710311ADF0E20730ULL;

template <typename Function>
Function ResolveExport(HMODULE module, const char* decorated_name) noexcept {
    if (!module) return nullptr;
    return reinterpret_cast<Function>(GetProcAddress(module, decorated_name));
}

bool HasParentTraversal(const std::filesystem::path& path) {
    return std::any_of(path.begin(), path.end(), [](const auto& component) {
        return component == L"..";
    });
}

bool HasExistingReparseComponent(const std::filesystem::path& base,
                                 const std::filesystem::path& relative) {
    auto current = base;
    for (const auto& component : relative) {
        current /= component;
        const auto attributes = GetFileAttributesW(current.c_str());
        if (attributes == INVALID_FILE_ATTRIBUTES) {
            const auto error = GetLastError();
            if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) {
                return false;
            }
            return true;
        }
        if ((attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0U) return true;
    }
    return false;
}

std::string ReadBoundedTextFile(const std::filesystem::path& path,
                                std::size_t limit, bool& too_large) {
    too_large = false;
    std::error_code error;
    const auto size = std::filesystem::file_size(path, error);
    if (error) return {};
    if (size > limit) {
        too_large = true;
        return {};
    }
    std::ifstream stream(path, std::ios::binary);
    if (!stream) return {};
    std::string text(static_cast<std::size_t>(size), '\0');
    if (!text.empty()) {
        stream.read(text.data(), static_cast<std::streamsize>(text.size()));
        if (!stream) return {};
    }
    return text;
}

const char* LevelText(LogLevel level) noexcept {
    switch (level) {
    case LogLevel::Debug:
        return "DEBUG";
    case LogLevel::Info:
        return "INFO";
    case LogLevel::Warning:
        return "WARN";
    case LogLevel::Error:
        return "ERROR";
    }
    return "UNKNOWN";
}

std::string OneLine(std::string text) {
    for (auto& character : text) {
        if (character == '\r' || character == '\n' || character == '\t') {
            character = ' ';
        }
    }
    return text;
}

std::optional<std::filesystem::path> ProcessImagePath() {
    std::vector<wchar_t> buffer(32768);
    const auto length = GetModuleFileNameW(nullptr, buffer.data(),
                                           static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) return std::nullopt;
    return std::filesystem::path(std::wstring(buffer.data(), length));
}

std::uint32_t ExecutableBuild(const std::filesystem::path& path) {
    DWORD ignored = 0;
    const auto size = GetFileVersionInfoSizeW(path.c_str(), &ignored);
    if (size == 0) return 0;
    std::vector<std::uint8_t> data(size);
    if (!GetFileVersionInfoW(path.c_str(), 0, size, data.data())) return 0;
    VS_FIXEDFILEINFO* version = nullptr;
    UINT version_size = 0;
    if (!VerQueryValueW(data.data(), L"\\",
                        reinterpret_cast<void**>(&version), &version_size) ||
        !version || version_size < sizeof(VS_FIXEDFILEINFO) ||
        version->dwSignature != 0xFEEF04BD) {
        return 0;
    }
    // GTA file versions use 1.0.<game build>.<revision>.
    return HIWORD(version->dwFileVersionLS);
}

std::string Sha256File(const std::filesystem::path& path) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    std::vector<std::uint8_t> object;
    std::vector<std::uint8_t> digest;
    std::string result;

    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM,
                                    nullptr, 0) < 0) {
        return result;
    }
    DWORD object_length = 0;
    DWORD digest_length = 0;
    DWORD returned = 0;
    if (BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                          reinterpret_cast<PUCHAR>(&object_length),
                          sizeof(object_length), &returned, 0) < 0 ||
        BCryptGetProperty(algorithm, BCRYPT_HASH_LENGTH,
                          reinterpret_cast<PUCHAR>(&digest_length),
                          sizeof(digest_length), &returned, 0) < 0 ||
        object_length == 0 || digest_length == 0) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
        return result;
    }
    object.resize(object_length);
    digest.resize(digest_length);
    if (BCryptCreateHash(algorithm, &hash, object.data(), object_length,
                         nullptr, 0, 0) < 0) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
        return result;
    }

    std::ifstream stream(path, std::ios::binary);
    std::array<char, 64 * 1024> buffer{};
    bool failed = !stream;
    while (!failed && stream) {
        stream.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        const auto count = stream.gcount();
        if (count > 0 &&
            BCryptHashData(hash, reinterpret_cast<PUCHAR>(buffer.data()),
                           static_cast<ULONG>(count), 0) < 0) {
            failed = true;
        }
    }
    if (!failed && BCryptFinishHash(hash, digest.data(), digest_length, 0) >= 0) {
        std::ostringstream encoded;
        encoded << std::hex << std::setfill('0');
        for (const auto byte : digest) {
            encoded << std::setw(2) << static_cast<unsigned int>(byte);
        }
        result = encoded.str();
    }
    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    return result;
}

bool EqualsIgnoreCase(const std::wstring& left, const wchar_t* right) {
    return _wcsicmp(left.c_str(), right) == 0;
}

bool IsHexDigit(char value) {
    return std::isxdigit(static_cast<unsigned char>(value)) != 0;
}

std::optional<std::uint8_t> HexByte(char high, char low) {
    if (!IsHexDigit(high) || !IsHexDigit(low)) return std::nullopt;
    const auto decode = [](char value) -> unsigned int {
        if (value >= '0' && value <= '9') return value - '0';
        value = static_cast<char>(std::tolower(static_cast<unsigned char>(value)));
        return 10U + static_cast<unsigned int>(value - 'a');
    };
    return static_cast<std::uint8_t>((decode(high) << 4U) | decode(low));
}

bool DecodePattern(const std::string& pattern, const std::string& mask,
                   std::vector<std::uint8_t>& bytes,
                   std::string& normalized_mask) {
    bytes.clear();
    normalized_mask.clear();
    const bool direct_mask = !pattern.empty() && pattern.size() == mask.size() &&
                             std::all_of(mask.begin(), mask.end(), [](char value) {
                                 return value == 'x' || value == 'X' || value == '?';
                             });
    if (direct_mask) {
        bytes.assign(pattern.begin(), pattern.end());
        normalized_mask = mask;
        return true;
    }

    std::istringstream tokens(pattern);
    std::string token;
    while (tokens >> token) {
        if (token == "?" || token == "??") {
            bytes.push_back(0);
            normalized_mask.push_back('?');
            continue;
        }
        if (token.size() != 2) return false;
        const auto decoded = HexByte(token[0], token[1]);
        if (!decoded.has_value()) return false;
        bytes.push_back(*decoded);
        normalized_mask.push_back('x');
    }
    if (bytes.empty()) return false;
    if (!mask.empty()) {
        std::string compact;
        std::copy_if(mask.begin(), mask.end(), std::back_inserter(compact),
                     [](char value) { return value == 'x' || value == 'X' || value == '?'; });
        if (compact.size() != bytes.size()) return false;
        normalized_mask = compact;
    }
    return true;
}

bool IsExecutableProtection(DWORD protection) noexcept {
    if ((protection & (PAGE_GUARD | PAGE_NOACCESS)) != 0) return false;
    const auto base = protection & 0xFFU;
    return base == PAGE_EXECUTE || base == PAGE_EXECUTE_READ ||
           base == PAGE_EXECUTE_READWRITE ||
           base == PAGE_EXECUTE_WRITECOPY;
}

} // namespace

bool ScriptHookApi::Bind() noexcept {
    failure_ = "ScriptHookV host API is not bound";
    script_hook_ = GetModuleHandleW(L"ScriptHookV.dll");
    if (!script_hook_) {
        failure_ = "ScriptHookV.dll is not loaded";
        return false;
    }
    script_register_ = ResolveExport<ScriptRegister>(
        script_hook_, "?scriptRegister@@YAXPEAUHINSTANCE__@@P6AXXZ@Z");
    script_unregister_ = ResolveExport<ScriptUnregister>(
        script_hook_, "?scriptUnregister@@YAXPEAUHINSTANCE__@@@Z");
    script_wait_ = ResolveExport<ScriptWait>(
        script_hook_, "?scriptWait@@YAXK@Z");
    native_init_ = ResolveExport<NativeInit>(
        script_hook_, "?nativeInit@@YAX_K@Z");
    native_push64_ = ResolveExport<NativePush64>(
        script_hook_, "?nativePush64@@YAX_K@Z");
    native_call_ = ResolveExport<NativeCall>(
        script_hook_, "?nativeCall@@YAPEA_KXZ");
    native_can_execute_ = ResolveExport<NativeCanExecute>(
        script_hook_, "?nativeCanExecuteInThisContext@@YA_NXZ");
    world_get_all_vehicles_ = ResolveExport<WorldGetAllVehicles>(
        script_hook_, "?worldGetAllVehicles@@YAHPEAHH@Z");

    if (!ReadyForScript()) {
        failure_ = "ScriptHookV is missing one or more required host exports";
        return false;
    }
    failure_ = "";
    return true;
}

bool ScriptHookApi::Register(HMODULE module,
                             ScriptMainCallback callback) const noexcept {
    if (!script_register_ || !module || !callback) return false;
    script_register_(module, callback);
    return true;
}

void ScriptHookApi::Unregister(HMODULE module) const noexcept {
    if (script_unregister_ && module) script_unregister_(module);
}

void ScriptHookApi::Wait(DWORD milliseconds) const noexcept {
    if (script_wait_) script_wait_(milliseconds);
}

bool ScriptHookApi::ReadyForScript() const noexcept {
    return script_register_ && script_unregister_ && script_wait_ &&
           native_init_ && native_push64_ && native_call_ &&
           world_get_all_vehicles_;
}

bool ScriptHookApi::CanInvokeNatives() const noexcept {
    return native_init_ && native_push64_ && native_call_ &&
           (!native_can_execute_ || native_can_execute_());
}

std::optional<bool>
ScriptHookApi::InvokeBool(std::uint64_t hash) const noexcept {
    if (!CanInvokeNatives()) return std::nullopt;
    native_init_(hash);
    const auto result = native_call_();
    if (!result) return std::nullopt;
    return (*result & 0xFFU) != 0;
}

std::optional<std::uint32_t>
ScriptHookApi::InvokeEntityHash(std::uint64_t hash,
                                std::int32_t entity) const noexcept {
    if (!CanInvokeNatives()) return std::nullopt;
    native_init_(hash);
    native_push64_(static_cast<std::uint32_t>(entity));
    const auto result = native_call_();
    if (!result) return std::nullopt;
    return static_cast<std::uint32_t>(*result);
}

std::optional<bool>
ScriptHookApi::InvokeEntityBool(std::uint64_t hash,
                                std::int32_t entity) const noexcept {
    if (!CanInvokeNatives()) return std::nullopt;
    native_init_(hash);
    native_push64_(static_cast<std::uint32_t>(entity));
    const auto result = native_call_();
    if (!result) return std::nullopt;
    return (*result & 0xFFU) != 0;
}

bool ScriptHookApi::InvokeEntityVoid(std::uint64_t hash,
                                     std::int32_t entity) const noexcept {
    if (!CanInvokeNatives()) return false;
    native_init_(hash);
    native_push64_(static_cast<std::uint32_t>(entity));
    return native_call_() != nullptr;
}

std::vector<std::int32_t> ScriptHookApi::EnumerateVehicleHandles() const {
    std::vector<std::int32_t> result;
    if (!world_get_all_vehicles_) return result;
    std::array<std::int32_t, kMaximumEnumeratedVehicles> handles{};
    const auto count = world_get_all_vehicles_(handles.data(),
                                               static_cast<int>(handles.size()));
    if (count <= 0) return result;
    const auto bounded = std::min<int>(count, static_cast<int>(handles.size()));
    result.reserve(static_cast<std::size_t>(bounded));
    for (int index = 0; index < bounded; ++index) {
        if (handles[static_cast<std::size_t>(index)] > 0) {
            result.push_back(handles[static_cast<std::size_t>(index)]);
        }
    }
    return result;
}

const char* ScriptHookApi::LastFailure() const noexcept {
    return failure_;
}

std::optional<RuntimePaths> ResolveRuntimePaths(HMODULE module) {
    if (!module) return std::nullopt;
    std::vector<wchar_t> buffer(32768);
    const auto length = GetModuleFileNameW(module, buffer.data(),
                                           static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) return std::nullopt;
    const auto module_path =
        std::filesystem::path(std::wstring(buffer.data(), length));
    RuntimePaths paths;
    paths.module_directory = module_path.parent_path();
    paths.data_directory = paths.module_directory / L"VehicleWorkbenchAxles";
    paths.settings_file = paths.data_directory / L"runtime.json";
    paths.configuration_directory = paths.data_directory / L"configs";
    paths.log_file = paths.data_directory / L"logs" /
                     L"VehicleWorkbenchAxles.log";
    return paths;
}

RuntimeSettings LoadRuntimeSettings(const RuntimePaths& paths,
                                    std::vector<ValidationIssue>& issues) {
    std::error_code error;
    if (!std::filesystem::is_regular_file(paths.settings_file, error)) {
        issues.push_back({"runtime-settings-defaulted",
                          "runtime.json was not found; safe defaults are in use",
                          "runtime.json", false});
        return {};
    }
    bool too_large = false;
    const auto text = ReadBoundedTextFile(paths.settings_file,
                                          kMaximumRuntimeJsonBytes, too_large);
    if (too_large) {
        issues.push_back({"runtime-settings-too-large",
                          "runtime.json exceeds the 1 MiB safety limit",
                          "runtime.json", true});
        return {};
    }
    if (text.empty()) {
        issues.push_back({"runtime-settings-read-failed",
                          "runtime.json could not be read",
                          "runtime.json", true});
        return {};
    }
    auto parsed = ParseRuntimeSettingsJson(text, issues, "runtime.json");
    return parsed.value_or(RuntimeSettings{});
}

std::filesystem::path ResolveLogPath(const RuntimePaths& paths,
                                     const RuntimeSettings& settings,
                                     bool& used_fallback) noexcept {
    used_fallback = false;
    try {
        const auto requested = std::filesystem::u8path(settings.log_file);
        if (requested.empty() || requested.is_absolute() ||
            requested.has_root_name() || requested.has_root_directory() ||
            HasParentTraversal(requested)) {
            used_fallback = true;
            return paths.log_file;
        }
        const auto& base = settings.schema_version == 1
                               ? paths.data_directory
                               : paths.module_directory;
        const auto normalized = (base / requested).lexically_normal();
        const auto relative = normalized.lexically_relative(
            base.lexically_normal());
        if (relative.empty() || relative.is_absolute() ||
            HasParentTraversal(relative) ||
            HasExistingReparseComponent(base, relative)) {
            used_fallback = true;
            return paths.log_file;
        }
        return normalized;
    } catch (...) {
        used_fallback = true;
        return paths.log_file;
    }
}

std::filesystem::path ResolveConfigurationPath(
    const RuntimePaths& paths, const RuntimeSettings& settings,
    bool& used_fallback) noexcept {
    used_fallback = false;
    try {
        const auto requested =
            std::filesystem::u8path(settings.configuration_directory);
        if (requested.empty() || requested.is_absolute() ||
            requested.has_root_name() || requested.has_root_directory() ||
            HasParentTraversal(requested)) {
            used_fallback = true;
            return paths.configuration_directory;
        }
        const auto& base = settings.schema_version == 1
                               ? paths.data_directory
                               : paths.module_directory;
        const auto normalized = (base / requested).lexically_normal();
        const auto relative = normalized.lexically_relative(
            base.lexically_normal());
        if (relative.empty() || relative.is_absolute() ||
            HasParentTraversal(relative) ||
            HasExistingReparseComponent(base, relative)) {
            used_fallback = true;
            return paths.configuration_directory;
        }
        return normalized;
    } catch (...) {
        used_fallback = true;
        return paths.configuration_directory;
    }
}

FileLogSink::FileLogSink(std::filesystem::path path) {
    std::error_code error;
    std::filesystem::create_directories(path.parent_path(), error);
    if (!error) stream_.open(path, std::ios::app | std::ios::binary);
}

void FileLogSink::Write(LogLevel level, const std::string& code,
                        const std::string& message) {
    if (!stream_) return;
    SYSTEMTIME now{};
    GetLocalTime(&now);
    stream_ << std::setfill('0') << std::setw(4) << now.wYear << '-'
            << std::setw(2) << now.wMonth << '-' << std::setw(2) << now.wDay
            << 'T' << std::setw(2) << now.wHour << ':' << std::setw(2)
            << now.wMinute << ':' << std::setw(2) << now.wSecond << '.'
            << std::setw(3) << now.wMilliseconds << ' ' << LevelText(level)
            << ' ' << OneLine(code) << ' ' << OneLine(message) << "\r\n";
    stream_.flush();
}

bool FileLogSink::IsOpen() const noexcept {
    return stream_.is_open();
}

ExecutableSignatureResolver::ExecutableSignatureResolver() {
    const auto module = reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr));
    if (!module) return;
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(module);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0) return;
    const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(
        module + static_cast<std::uintptr_t>(dos->e_lfanew));
    if (nt->Signature != IMAGE_NT_SIGNATURE ||
        nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC ||
        nt->OptionalHeader.SizeOfImage == 0) {
        return;
    }
    module_begin_ = module;
    module_end_ = module + nt->OptionalHeader.SizeOfImage;
    const auto* section = IMAGE_FIRST_SECTION(nt);
    for (WORD index = 0; index < nt->FileHeader.NumberOfSections; ++index) {
        const auto& item = section[index];
        if ((item.Characteristics & IMAGE_SCN_MEM_EXECUTE) == 0 ||
            item.Misc.VirtualSize == 0) {
            continue;
        }
        const auto begin = module + item.VirtualAddress;
        const auto size = static_cast<std::uintptr_t>(item.Misc.VirtualSize);
        if (begin < module_begin_ || begin > module_end_ ||
            size > module_end_ - begin) {
            continue;
        }
        executable_ranges_.push_back({begin, begin + size});
    }
}

std::optional<std::uintptr_t>
ExecutableSignatureResolver::Resolve(const std::string& pattern,
                                     const std::string& mask) {
    std::vector<std::uint8_t> bytes;
    std::string normalized_mask;
    if (!DecodePattern(pattern, mask, bytes, normalized_mask)) {
        return std::nullopt;
    }
    std::optional<std::uintptr_t> unique_match;
    const auto anchor_iterator = std::find_if(
        normalized_mask.begin(), normalized_mask.end(),
        [](char value) { return value != '?'; });
    const auto anchor = anchor_iterator == normalized_mask.end()
                            ? bytes.size()
                            : static_cast<std::size_t>(
                                  anchor_iterator - normalized_mask.begin());
    for (const auto& range : executable_ranges_) {
        if (range.end <= range.begin ||
            bytes.size() > static_cast<std::size_t>(range.end - range.begin)) {
            continue;
        }
        const auto last = range.end - bytes.size();
        auto address = range.begin;
        while (address <= last) {
            // Jump between occurrences of one required byte instead of
            // testing the full pattern at every executable byte. Wildcard
            // semantics and unique-match rejection remain unchanged.
            if (anchor < bytes.size()) {
                const auto search_begin = address + anchor;
                const auto search_last = last + anchor;
                const auto remaining = static_cast<std::size_t>(
                    search_last - search_begin + 1U);
                const auto* found = static_cast<const std::uint8_t*>(
                    std::memchr(reinterpret_cast<const void*>(search_begin),
                                bytes[anchor], remaining));
                if (!found) break;
                address = reinterpret_cast<std::uintptr_t>(found) - anchor;
            }
            const auto* candidate = reinterpret_cast<const std::uint8_t*>(address);
            bool matched = true;
            for (std::size_t index = 0; index < bytes.size(); ++index) {
                if (normalized_mask[index] != '?' &&
                    candidate[index] != bytes[index]) {
                    matched = false;
                    break;
                }
            }
            if (matched) {
                if (unique_match.has_value()) return std::nullopt;
                unique_match = address;
            }
            ++address;
        }
    }
    return unique_match;
}

bool ExecutableSignatureResolver::IsExecutable(
    std::uintptr_t address, std::size_t minimum_bytes) const {
    if (!IsInGameModule(address, minimum_bytes)) return false;
    const auto in_section = std::any_of(
        executable_ranges_.begin(), executable_ranges_.end(),
        [&](const auto& range) {
            return address >= range.begin && address <= range.end &&
                   minimum_bytes <= range.end - address;
        });
    if (!in_section) return false;
    MEMORY_BASIC_INFORMATION information{};
    if (VirtualQuery(reinterpret_cast<const void*>(address), &information,
                     sizeof(information)) != sizeof(information) ||
        information.State != MEM_COMMIT ||
        !IsExecutableProtection(information.Protect)) {
        return false;
    }
    const auto region_end = reinterpret_cast<std::uintptr_t>(
                                information.BaseAddress) +
                            information.RegionSize;
    return address <= region_end && minimum_bytes <= region_end - address;
}

bool ExecutableSignatureResolver::IsInGameModule(
    std::uintptr_t address, std::size_t minimum_bytes) const {
    return module_begin_ != 0 && address >= module_begin_ &&
           address <= module_end_ && minimum_bytes <= module_end_ - address;
}

bool ExecutableSignatureResolver::BytesMatch(
    std::uintptr_t address,
    const std::vector<std::uint8_t>& expected) const {
    return !expected.empty() && IsInGameModule(address, expected.size()) &&
           std::memcmp(reinterpret_cast<const void*>(address), expected.data(),
                       expected.size()) == 0;
}

StoryVehicleHost::StoryVehicleHost(const ScriptHookApi& api,
                                   Edition compiled_edition, ILogSink& log)
    : api_(api), compiled_edition_(compiled_edition), log_(log) {
    identity_.edition = compiled_edition_;
    const auto image = ProcessImagePath();
    if (!image.has_value()) {
        identity_.edition = Edition::Unknown;
        return;
    }
    const auto filename = image->filename().wstring();
    const bool expected =
        (compiled_edition_ == Edition::Legacy &&
         EqualsIgnoreCase(filename, L"GTA5.exe")) ||
        (compiled_edition_ == Edition::Enhanced &&
         EqualsIgnoreCase(filename, L"GTA5_Enhanced.exe"));
    if (!expected) identity_.edition = Edition::Unknown;
    image_path_ = *image;
    identity_.build = ExecutableBuild(*image);
}

GameIdentity StoryVehicleHost::DetectGame() const {
    // Hashing the GTA executable can touch hundreds of megabytes. Defer it
    // until a validated configuration actually requires build-profile
    // resolution instead of charging every inactive/no-config installation at
    // ScriptHook startup.
    if (!fingerprint_resolved_) {
        fingerprint_resolved_ = true;
        if (identity_.edition != Edition::Unknown && !image_path_.empty()) {
            identity_.executable_fingerprint = Sha256File(image_path_);
        }
    }
    return identity_;
}

bool StoryVehicleHost::IsOnlineSession() const {
    const auto session = api_.InvokeBool(kNetworkIsSessionActive);
    const auto transition = api_.InvokeBool(kNetworkIsInTransition);
    if (!session.has_value() || !transition.has_value()) {
        if (!native_failure_logged_) {
            log_.Write(LogLevel::Error, "online-guard-unavailable",
                       "Network session state could not be queried; runtime "
                       "disabled fail-closed");
            native_failure_logged_ = true;
        }
        return true;
    }
    return *session || *transition;
}

std::vector<VehicleSnapshot> StoryVehicleHost::EnumerateVehicles() {
    std::vector<VehicleSnapshot> result;
    const auto handles = api_.EnumerateVehicleHandles();
    result.reserve(handles.size());
    for (const auto handle : handles) {
        // worldGetAllVehicles already produced a live world handle. A second
        // DOES_ENTITY_EXIST native for every vehicle was redundant;
        // GET_ENTITY_MODEL below remains the authoritative snapshot filter.
        const auto snapshot = Snapshot(handle, false);
        if (snapshot.has_value()) result.push_back(*snapshot);
    }
    return result;
}

std::optional<VehicleSnapshot>
StoryVehicleHost::LookupVehicle(std::uint64_t entity_id) {
    if (entity_id == 0 ||
        entity_id > static_cast<std::uint64_t>(
                        std::numeric_limits<std::int32_t>::max())) {
        return std::nullopt;
    }
    // GET_ENTITY_MODEL returns zero for a released handle and supplies the
    // identity check required by the runtime. Avoid a redundant existence
    // native on every 100 ms maintenance lookup.
    return Snapshot(static_cast<std::int32_t>(entity_id), false);
}

bool StoryVehicleHost::SupportsPhysicsActivation() const noexcept {
    return api_.CanInvokeNatives();
}

bool StoryVehicleHost::ActivatePhysics(const VehicleSnapshot& vehicle) {
    if (vehicle.entity_id == 0 ||
        vehicle.entity_id > static_cast<std::uint64_t>(
                                std::numeric_limits<std::int32_t>::max())) {
        return false;
    }
    const auto handle = static_cast<std::int32_t>(vehicle.entity_id);
    const auto exists = api_.InvokeEntityBool(kDoesEntityExist, handle);
    return exists.has_value() && *exists &&
           api_.InvokeEntityVoid(kActivatePhysics, handle);
}

std::optional<VehicleSnapshot>
StoryVehicleHost::Snapshot(std::int32_t handle,
                           bool verify_existence) const {
    if (handle <= 0) return std::nullopt;
    if (verify_existence) {
        const auto exists = api_.InvokeEntityBool(kDoesEntityExist, handle);
        if (!exists.has_value() || !*exists) return std::nullopt;
    }
    const auto model = api_.InvokeEntityHash(kGetEntityModel, handle);
    if (!model.has_value() || *model == 0) return std::nullopt;
    VehicleSnapshot snapshot;
    snapshot.entity_id = static_cast<std::uint32_t>(handle);
    snapshot.model_hash = *model;
    // Handles can be recycled, but model changes and the core recovery pass
    // already force revalidation. No game pointer is retained here.
    snapshot.wheel_generation =
        (static_cast<std::uint64_t>(*model) << 32U) |
        static_cast<std::uint32_t>(handle);
    return snapshot;
}

} // namespace vwa::story
