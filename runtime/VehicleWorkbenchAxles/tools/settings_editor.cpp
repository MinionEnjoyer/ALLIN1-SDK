#include "vehicle_workbench_axles/configuration.hpp"
#include "vehicle_workbench_axles/runtime_settings_document.hpp"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <filesystem>
#include <fstream>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

namespace {

constexpr int kConfigurationEdit = 1001;
constexpr int kLogEdit = 1002;
constexpr int kSaveButton = 1003;
constexpr int kDefaultsButton = 1004;
constexpr int kCloseButton = 1005;
constexpr std::uintmax_t kMaximumSettingsBytes = 1024U * 1024U;

HINSTANCE g_instance = nullptr;
HWND g_configuration_edit = nullptr;
HWND g_log_edit = nullptr;
HWND g_status = nullptr;
HWND g_save = nullptr;
HWND g_defaults = nullptr;
HWND g_close = nullptr;
HFONT g_font = nullptr;
std::filesystem::path g_settings_path;
vwa::RuntimeSettings g_settings;

std::wstring WideFromUtf8(const std::string& value) {
    if (value.empty())
        return {};
    const int size =
        MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                            static_cast<int>(value.size()), nullptr, 0);
    if (size <= 0)
        throw std::runtime_error("Text is not valid UTF-8");
    std::wstring result(static_cast<std::size_t>(size), L'\0');
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                            static_cast<int>(value.size()), result.data(),
                            size) != size) {
        throw std::runtime_error("Text could not be converted to UTF-16");
    }
    return result;
}

std::string Utf8FromWide(const std::wstring& value) {
    if (value.empty())
        return {};
    const int size = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
        static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (size <= 0)
        throw std::runtime_error("Text is not valid UTF-16");
    std::string result(static_cast<std::size_t>(size), '\0');
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                            static_cast<int>(value.size()), result.data(), size,
                            nullptr, nullptr) != size) {
        throw std::runtime_error("Text could not be converted to UTF-8");
    }
    return result;
}

std::filesystem::path ExecutableDirectory() {
    std::vector<wchar_t> buffer(1024U);
    for (;;) {
        const DWORD length = GetModuleFileNameW(
            nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (length == 0U) {
            throw std::runtime_error("The executable path could not be read");
        }
        if (length < buffer.size() - 1U) {
            return std::filesystem::path(
                       std::wstring(buffer.data(),
                                    static_cast<std::size_t>(length)))
                .parent_path();
        }
        if (buffer.size() >= 32768U) {
            throw std::runtime_error("The executable path is too long");
        }
        buffer.resize(buffer.size() * 2U);
    }
}

std::wstring WindowText(HWND window) {
    const int length = GetWindowTextLengthW(window);
    if (length < 0 || length > 4096) {
        throw std::runtime_error("A settings value is too long");
    }
    std::wstring value(static_cast<std::size_t>(length) + 1U, L'\0');
    const int copied = GetWindowTextW(window, value.data(), length + 1);
    value.resize(static_cast<std::size_t>(copied));
    return value;
}

std::wstring IssueText(const std::vector<vwa::ValidationIssue>& issues) {
    std::wstring result;
    for (const auto& issue : issues) {
        if (!result.empty())
            result += L"\r\n";
        result += WideFromUtf8(issue.code + ": " + issue.message);
    }
    return result.empty() ? L"The settings could not be validated." : result;
}

void SetEditorValues(const vwa::RuntimeSettings& settings) {
    SetWindowTextW(g_configuration_edit,
                   WideFromUtf8(settings.configuration_directory).c_str());
    SetWindowTextW(g_log_edit, WideFromUtf8(settings.log_file).c_str());
}

std::optional<std::string>
PortableDocument(const vwa::RuntimeSettings& settings,
                 std::vector<vwa::ValidationIssue>& issues) {
    return vwa::SerializePortableRuntimeSettingsJson(settings, issues,
                                                     "runtime.json");
}

void EnableSaving(bool enabled) {
    EnableWindow(g_save, enabled ? TRUE : FALSE);
}

void UseDefaults() {
    g_settings = vwa::RuntimeSettings{};
    SetEditorValues(g_settings);
    EnableSaving(true);
    SetWindowTextW(
        g_status,
        L"Defaults are ready. Select Save settings to write runtime.json.");
}

void LoadSettings() {
    if (!std::filesystem::exists(g_settings_path)) {
        UseDefaults();
        SetWindowTextW(
            g_status,
            L"No runtime.json exists yet. The documented defaults are shown.");
        return;
    }
    std::error_code error;
    const auto size = std::filesystem::file_size(g_settings_path, error);
    if (error || size > kMaximumSettingsBytes) {
        EnableSaving(false);
        SetWindowTextW(g_status,
                       L"runtime.json is unreadable or exceeds 1 MiB. "
                       L"Use defaults to repair it.");
        return;
    }
    std::ifstream input(g_settings_path, std::ios::binary);
    if (!input) {
        EnableSaving(false);
        SetWindowTextW(
            g_status,
            L"runtime.json could not be opened. Use defaults to repair it.");
        return;
    }
    const std::string text{std::istreambuf_iterator<char>(input),
                           std::istreambuf_iterator<char>()};
    std::vector<vwa::ValidationIssue> issues;
    const auto parsed =
        vwa::ParseRuntimeSettingsJson(text, issues, "runtime.json");
    if (!parsed.has_value()) {
        EnableSaving(false);
        SetWindowTextW(g_status, IssueText(issues).c_str());
        return;
    }

    // Reparse the canonical document so schema-1 paths are visibly migrated
    // to their equivalent portable GTA-root-relative schema-2 values.
    issues.clear();
    const auto portable = PortableDocument(*parsed, issues);
    if (!portable.has_value()) {
        EnableSaving(false);
        SetWindowTextW(g_status, IssueText(issues).c_str());
        return;
    }
    issues.clear();
    const auto normalized =
        vwa::ParseRuntimeSettingsJson(*portable, issues, "runtime.json");
    if (!normalized.has_value()) {
        EnableSaving(false);
        SetWindowTextW(g_status, IssueText(issues).c_str());
        return;
    }
    g_settings = *normalized;
    SetEditorValues(g_settings);
    EnableSaving(true);
    SetWindowTextW(
        g_status,
        L"Loaded runtime.json. Paths below are relative to the GTA folder.");
}

void SaveSettings(HWND owner) {
    try {
        auto candidate = g_settings;
        candidate.schema_version = vwa::kRuntimeSettingsSchemaVersion;
        candidate.configuration_directory =
            Utf8FromWide(WindowText(g_configuration_edit));
        candidate.log_file = Utf8FromWide(WindowText(g_log_edit));
        std::vector<vwa::ValidationIssue> issues;
        const auto document = PortableDocument(candidate, issues);
        if (!document.has_value()) {
            MessageBoxW(owner, IssueText(issues).c_str(),
                        L"Settings were not saved", MB_OK | MB_ICONWARNING);
            return;
        }

        std::error_code error;
        std::filesystem::create_directories(g_settings_path.parent_path(),
                                            error);
        if (error) {
            throw std::runtime_error(
                "The VehicleWorkbenchAxles folder could not be created");
        }
        const auto temporary = g_settings_path.wstring() + L".tmp";
        {
            std::ofstream output(std::filesystem::path(temporary),
                                 std::ios::binary | std::ios::trunc);
            output.write(document->data(),
                         static_cast<std::streamsize>(document->size()));
            output.flush();
            if (!output) {
                throw std::runtime_error(
                    "The temporary settings file could not be written");
            }
        }
        if (!MoveFileExW(temporary.c_str(), g_settings_path.c_str(),
                         MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
            DeleteFileW(temporary.c_str());
            throw std::runtime_error(
                "runtime.json could not be replaced atomically");
        }
        issues.clear();
        const auto normalized =
            vwa::ParseRuntimeSettingsJson(*document, issues, "runtime.json");
        if (normalized.has_value())
            g_settings = *normalized;
        SetEditorValues(g_settings);
        SetWindowTextW(g_status,
                       L"Saved runtime.json. Changes apply the next time "
                       L"the controller loads.");
    } catch (const std::exception& error) {
        MessageBoxW(owner, WideFromUtf8(error.what()).c_str(),
                    L"Settings were not saved", MB_OK | MB_ICONERROR);
    }
}

void ApplyFont(HWND window) {
    SendMessageW(window, WM_SETFONT, reinterpret_cast<WPARAM>(g_font), TRUE);
}

void Layout(HWND window) {
    RECT rectangle{};
    GetClientRect(window, &rectangle);
    const int width = rectangle.right - rectangle.left;
    const int height = rectangle.bottom - rectangle.top;
    constexpr int margin = 22;
    constexpr int label_height = 20;
    constexpr int edit_height = 28;
    constexpr int button_width = 116;
    constexpr int button_height = 30;

    HWND configuration_label = GetDlgItem(window, 1101);
    HWND log_label = GetDlgItem(window, 1102);
    HWND explanation = GetDlgItem(window, 1103);
    MoveWindow(explanation, margin, 18, width - margin * 2, 38, TRUE);
    MoveWindow(configuration_label, margin, 66, width - margin * 2,
               label_height, TRUE);
    MoveWindow(g_configuration_edit, margin, 88, width - margin * 2,
               edit_height, TRUE);
    MoveWindow(log_label, margin, 128, width - margin * 2, label_height, TRUE);
    MoveWindow(g_log_edit, margin, 150, width - margin * 2, edit_height, TRUE);
    MoveWindow(g_status, margin, 192, width - margin * 2,
               std::max(40, height - 244), TRUE);
    const int button_y = height - margin - button_height;
    MoveWindow(g_save, margin, button_y, button_width, button_height, TRUE);
    MoveWindow(g_defaults, margin + button_width + 10, button_y, button_width,
               button_height, TRUE);
    MoveWindow(g_close, width - margin - button_width, button_y, button_width,
               button_height, TRUE);
}

HWND CreateControl(DWORD extended_style, const wchar_t* class_name,
                   const wchar_t* text, DWORD style, int id, HWND parent) {
    HWND control = CreateWindowExW(
        extended_style, class_name, text, WS_CHILD | WS_VISIBLE | style, 0, 0,
        0, 0, parent, reinterpret_cast<HMENU>(static_cast<std::intptr_t>(id)),
        g_instance, nullptr);
    if (!control)
        throw std::runtime_error("A settings control could not be created");
    ApplyFont(control);
    return control;
}

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wparam,
                                 LPARAM lparam) {
    try {
        switch (message) {
        case WM_CREATE: {
            g_font = static_cast<HFONT>(GetStockObject(DEFAULT_GUI_FONT));
            CreateControl(
                0, L"STATIC",
                L"Choose where the global axle controller finds vehicle "
                L"configs and writes its log. "
                L"Use portable paths below the GTA installation folder.",
                SS_LEFT, 1103, window);
            CreateControl(0, L"STATIC",
                          L"Vehicle configuration folder (example: "
                          L"scripts/MyPack/VehicleSettings)",
                          SS_LEFT, 1101, window);
            g_configuration_edit = CreateControl(WS_EX_CLIENTEDGE, L"EDIT", L"",
                                                 ES_AUTOHSCROLL | WS_TABSTOP,
                                                 kConfigurationEdit, window);
            SendMessageW(g_configuration_edit, EM_SETLIMITTEXT, 4096, 0);
            CreateControl(0, L"STATIC",
                          L"Log file (example: scripts/MyPack/Axles.log)",
                          SS_LEFT, 1102, window);
            g_log_edit =
                CreateControl(WS_EX_CLIENTEDGE, L"EDIT", L"",
                              ES_AUTOHSCROLL | WS_TABSTOP, kLogEdit, window);
            SendMessageW(g_log_edit, EM_SETLIMITTEXT, 4096, 0);
            g_status = CreateControl(0, L"STATIC", L"Loading runtime.json...",
                                     SS_LEFT, 1104, window);
            g_save = CreateControl(0, L"BUTTON", L"Save settings",
                                   BS_DEFPUSHBUTTON | WS_TABSTOP, kSaveButton,
                                   window);
            g_defaults = CreateControl(0, L"BUTTON", L"Use defaults",
                                       BS_PUSHBUTTON | WS_TABSTOP,
                                       kDefaultsButton, window);
            g_close =
                CreateControl(0, L"BUTTON", L"Close",
                              BS_PUSHBUTTON | WS_TABSTOP, kCloseButton, window);
            g_settings_path = ExecutableDirectory() / L"VehicleWorkbenchAxles" /
                              L"runtime.json";
            Layout(window);
            LoadSettings();
            return 0;
        }
        case WM_SIZE:
            Layout(window);
            return 0;
        case WM_GETMINMAXINFO: {
            auto* info = reinterpret_cast<MINMAXINFO*>(lparam);
            info->ptMinTrackSize.x = 600;
            info->ptMinTrackSize.y = 340;
            return 0;
        }
        case WM_COMMAND:
            switch (LOWORD(wparam)) {
            case kSaveButton:
                SaveSettings(window);
                return 0;
            case kDefaultsButton:
                UseDefaults();
                return 0;
            case kCloseButton:
                DestroyWindow(window);
                return 0;
            default:
                break;
            }
            break;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
        default:
            break;
        }
    } catch (const std::exception& error) {
        MessageBoxW(window, WideFromUtf8(error.what()).c_str(),
                    L"Settings editor error", MB_OK | MB_ICONERROR);
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

} // namespace

int WINAPI WinMain(HINSTANCE instance, HINSTANCE, LPSTR, int show_command) {
    g_instance = instance;
    constexpr wchar_t kClassName[] = L"VehicleWorkbenchAxles.Settings.Window";
    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.style = CS_HREDRAW | CS_VREDRAW;
    window_class.lpfnWndProc = WindowProcedure;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
    window_class.hIconSm = window_class.hIcon;
    window_class.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    window_class.lpszClassName = kClassName;
    if (!RegisterClassExW(&window_class))
        return 1;

    HWND window = CreateWindowExW(
        0, kClassName, L"Vehicle Workbench Axle Controller Settings",
        WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, 760, 390, nullptr,
        nullptr, instance, nullptr);
    if (!window)
        return 1;
    ShowWindow(window, show_command);
    UpdateWindow(window);

    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        if (!IsDialogMessageW(window, &message)) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    return static_cast<int>(message.wParam);
}
