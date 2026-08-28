#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace vwa {

enum class Edition {
    Legacy,
    Enhanced,
    Unknown,
};

inline const char* ToString(Edition edition) noexcept {
    switch (edition) {
    case Edition::Legacy:
        return "story-legacy";
    case Edition::Enhanced:
        return "story-enhanced";
    default:
        return "unknown";
    }
}

struct GameIdentity {
    Edition edition{Edition::Unknown};
    std::uint32_t build{0};
    std::string executable_fingerprint;
};

struct VehicleSnapshot {
    // This is a host-owned, opaque entity identity.  It is never treated as a
    // persistent game pointer by the shared core.
    std::uint64_t entity_id{0};
    std::uint32_t model_hash{0};
    std::uint64_t wheel_generation{0};
};

struct WheelLocalPosition {
    double lateral{0.0};
    double longitudinal{0.0};
    double vertical{0.0};
};

enum class LogLevel {
    Debug,
    Info,
    Warning,
    Error,
};

class ILogSink {
public:
    virtual ~ILogSink() = default;
    virtual void Write(LogLevel level, const std::string& code,
                       const std::string& message) = 0;
};

struct ValidationIssue {
    std::string code;
    std::string message;
    std::string source_name;
    bool fatal{true};
};

} // namespace vwa
