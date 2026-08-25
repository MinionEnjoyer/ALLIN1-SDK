#include "json.hpp"

#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <sstream>

namespace vwa::json {

namespace {

class Parser final {
public:
    explicit Parser(std::string_view source) : source_(source) {}

    Value ParseDocument() {
        SkipWhitespace();
        auto value = ParseValue();
        SkipWhitespace();
        if (position_ != source_.size()) {
            Fail("unexpected trailing content");
        }
        return value;
    }

private:
    Value ParseValue() {
        if (position_ >= source_.size()) {
            Fail("expected a JSON value");
        }
        switch (source_[position_]) {
        case '{':
            return ParseObject();
        case '[':
            return ParseArray();
        case '"':
            return Value(ParseString());
        case 't':
            ConsumeLiteral("true");
            return Value(true);
        case 'f':
            ConsumeLiteral("false");
            return Value(false);
        case 'n':
            ConsumeLiteral("null");
            return Value(nullptr);
        default:
            if (source_[position_] == '-' ||
                std::isdigit(static_cast<unsigned char>(source_[position_]))) {
                return Value(ParseNumber());
            }
            Fail("unexpected token");
        }
    }

    Value ParseObject() {
        Expect('{');
        SkipWhitespace();
        Value::Object result;
        if (ConsumeIf('}')) {
            return Value(std::move(result));
        }
        while (true) {
            if (position_ >= source_.size() || source_[position_] != '"') {
                Fail("object key must be a string");
            }
            const auto key = ParseString();
            SkipWhitespace();
            Expect(':');
            SkipWhitespace();
            const auto [unused, inserted] =
                result.emplace(key, ParseValue());
            (void)unused;
            if (!inserted) {
                Fail("duplicate object key '" + key + "'");
            }
            SkipWhitespace();
            if (ConsumeIf('}')) {
                break;
            }
            Expect(',');
            SkipWhitespace();
        }
        return Value(std::move(result));
    }

    Value ParseArray() {
        Expect('[');
        SkipWhitespace();
        Value::Array result;
        if (ConsumeIf(']')) {
            return Value(std::move(result));
        }
        while (true) {
            result.emplace_back(ParseValue());
            SkipWhitespace();
            if (ConsumeIf(']')) {
                break;
            }
            Expect(',');
            SkipWhitespace();
        }
        return Value(std::move(result));
    }

    std::string ParseString() {
        Expect('"');
        std::string result;
        while (position_ < source_.size()) {
            const char current = source_[position_++];
            if (current == '"') {
                return result;
            }
            if (static_cast<unsigned char>(current) < 0x20) {
                Fail("unescaped control character in string");
            }
            if (current != '\\') {
                result.push_back(current);
                continue;
            }
            if (position_ >= source_.size()) {
                Fail("incomplete string escape");
            }
            const char escaped = source_[position_++];
            switch (escaped) {
            case '"': result.push_back('"'); break;
            case '\\': result.push_back('\\'); break;
            case '/': result.push_back('/'); break;
            case 'b': result.push_back('\b'); break;
            case 'f': result.push_back('\f'); break;
            case 'n': result.push_back('\n'); break;
            case 'r': result.push_back('\r'); break;
            case 't': result.push_back('\t'); break;
            case 'u':
                AppendUnicodeEscape(result);
                break;
            default:
                Fail("unsupported string escape");
            }
        }
        Fail("unterminated string");
    }

    static int HexValue(char value) noexcept {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return value - 'a' + 10;
        if (value >= 'A' && value <= 'F') return value - 'A' + 10;
        return -1;
    }

    void AppendUnicodeEscape(std::string& result) {
        if (position_ + 4 > source_.size()) {
            Fail("incomplete unicode escape");
        }
        unsigned codepoint = 0;
        for (int index = 0; index < 4; ++index) {
            const int digit = HexValue(source_[position_++]);
            if (digit < 0) {
                Fail("invalid unicode escape");
            }
            codepoint = (codepoint << 4U) | static_cast<unsigned>(digit);
        }
        // Configuration identifiers and bone names are ASCII in practice, but
        // accepting BMP UTF-8 keeps the JSON reader standards-friendly.
        if (codepoint <= 0x7F) {
            result.push_back(static_cast<char>(codepoint));
        } else if (codepoint <= 0x7FF) {
            result.push_back(static_cast<char>(0xC0U | (codepoint >> 6U)));
            result.push_back(static_cast<char>(0x80U | (codepoint & 0x3FU)));
        } else if (codepoint >= 0xD800 && codepoint <= 0xDFFF) {
            Fail("surrogate unicode escapes are not supported");
        } else {
            result.push_back(static_cast<char>(0xE0U | (codepoint >> 12U)));
            result.push_back(
                static_cast<char>(0x80U | ((codepoint >> 6U) & 0x3FU)));
            result.push_back(static_cast<char>(0x80U | (codepoint & 0x3FU)));
        }
    }

    double ParseNumber() {
        const std::size_t start = position_;
        if (ConsumeIf('-') && position_ >= source_.size()) {
            Fail("incomplete number");
        }
        if (ConsumeIf('0')) {
            if (position_ < source_.size() &&
                std::isdigit(static_cast<unsigned char>(source_[position_]))) {
                Fail("leading zero in number");
            }
        } else {
            ConsumeDigits(true);
        }
        if (ConsumeIf('.')) {
            ConsumeDigits(true);
        }
        if (position_ < source_.size() &&
            (source_[position_] == 'e' || source_[position_] == 'E')) {
            ++position_;
            if (position_ < source_.size() &&
                (source_[position_] == '+' || source_[position_] == '-')) {
                ++position_;
            }
            ConsumeDigits(true);
        }
        const std::string token(source_.substr(start, position_ - start));
        char* end = nullptr;
        errno = 0;
        const double result = std::strtod(token.c_str(), &end);
        if (errno == ERANGE || end != token.c_str() + token.size() ||
            !std::isfinite(result)) {
            Fail("number is outside the supported finite range");
        }
        return result;
    }

    void ConsumeDigits(bool require_one) {
        const std::size_t start = position_;
        while (position_ < source_.size() &&
               std::isdigit(static_cast<unsigned char>(source_[position_]))) {
            ++position_;
        }
        if (require_one && start == position_) {
            Fail("expected a decimal digit");
        }
    }

    void ConsumeLiteral(std::string_view literal) {
        if (source_.substr(position_, literal.size()) != literal) {
            Fail("invalid literal");
        }
        position_ += literal.size();
    }

    bool ConsumeIf(char expected) noexcept {
        if (position_ < source_.size() && source_[position_] == expected) {
            ++position_;
            return true;
        }
        return false;
    }

    void Expect(char expected) {
        if (!ConsumeIf(expected)) {
            std::string message = "expected '";
            message.push_back(expected);
            message.push_back('\'');
            Fail(message);
        }
    }

    void SkipWhitespace() noexcept {
        while (position_ < source_.size()) {
            const char current = source_[position_];
            if (current != ' ' && current != '\t' && current != '\r' &&
                current != '\n') {
                break;
            }
            ++position_;
        }
    }

    [[noreturn]] void Fail(const std::string& message) const {
        std::ostringstream output;
        output << "JSON parse error at byte " << position_ << ": " << message;
        throw Error(output.str());
    }

    std::string_view source_;
    std::size_t position_{0};
};

template <typename T>
const T& Require(const Value::Storage& storage, const char* expected) {
    const auto* value = std::get_if<T>(&storage);
    if (value == nullptr) {
        throw Error(std::string("JSON value is not ") + expected);
    }
    return *value;
}

} // namespace

bool Value::IsNull() const noexcept {
    return std::holds_alternative<std::nullptr_t>(storage_);
}
bool Value::IsBool() const noexcept { return std::holds_alternative<bool>(storage_); }
bool Value::IsNumber() const noexcept { return std::holds_alternative<double>(storage_); }
bool Value::IsString() const noexcept {
    return std::holds_alternative<std::string>(storage_);
}
bool Value::IsObject() const noexcept { return std::holds_alternative<Object>(storage_); }
bool Value::IsArray() const noexcept { return std::holds_alternative<Array>(storage_); }
bool Value::AsBool() const { return Require<bool>(storage_, "a boolean"); }
double Value::AsNumber() const { return Require<double>(storage_, "a number"); }
const std::string& Value::AsString() const {
    return Require<std::string>(storage_, "a string");
}
const Value::Object& Value::AsObject() const {
    return Require<Object>(storage_, "an object");
}
const Value::Array& Value::AsArray() const {
    return Require<Array>(storage_, "an array");
}

const Value* Value::Find(const std::string& key) const noexcept {
    const auto* object = std::get_if<Object>(&storage_);
    if (object == nullptr) {
        return nullptr;
    }
    const auto found = object->find(key);
    return found == object->end() ? nullptr : &found->second;
}

Value Parse(std::string_view text) { return Parser(text).ParseDocument(); }

} // namespace vwa::json
