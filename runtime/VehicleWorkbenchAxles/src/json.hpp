#pragma once

#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace vwa::json {

class Error final : public std::runtime_error {
public:
    explicit Error(const std::string& message) : std::runtime_error(message) {}
};

class Value {
public:
    using Object = std::map<std::string, Value>;
    using Array = std::vector<Value>;
    using Storage =
        std::variant<std::nullptr_t, bool, double, std::string, Object, Array>;

    explicit Value(Storage storage) : storage_(std::move(storage)) {}

    bool IsNull() const noexcept;
    bool IsBool() const noexcept;
    bool IsNumber() const noexcept;
    bool IsString() const noexcept;
    bool IsObject() const noexcept;
    bool IsArray() const noexcept;

    bool AsBool() const;
    double AsNumber() const;
    const std::string& AsString() const;
    const Object& AsObject() const;
    const Array& AsArray() const;

    const Value* Find(const std::string& key) const noexcept;

private:
    Storage storage_;
};

Value Parse(std::string_view text);

} // namespace vwa::json
