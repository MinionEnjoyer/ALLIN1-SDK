#include "vehicle_workbench_axles/configuration.hpp"

#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

#ifndef VWA_RUNTIME_VERSION
#define VWA_RUNTIME_VERSION "0.0.0"
#endif

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: VehicleWorkbenchAxlesConfigValidator <config.json>...\n";
        return 64;
    }

    bool failed = false;
    for (int index = 1; index < argc; ++index) {
        const std::string source_name(argv[index]);
        std::ifstream stream(source_name, std::ios::binary);
        if (!stream) {
            std::cerr << source_name << ": configuration could not be opened\n";
            failed = true;
            continue;
        }
        const std::string text((std::istreambuf_iterator<char>(stream)),
                               std::istreambuf_iterator<char>());
        std::vector<vwa::ValidationIssue> issues;
        const auto configuration = vwa::ParseConfigurationJson(
            text, VWA_RUNTIME_VERSION, issues, source_name);
        for (const auto& issue : issues) {
            std::cerr << source_name << ": " << issue.code << ": "
                      << issue.message << '\n';
        }
        if (!configuration.has_value()) {
            failed = true;
            continue;
        }
        std::cout << source_name << ": " << configuration->model_name << ' '
                  << configuration->axles.size() << " axles "
                  << configuration->wheel_index_map.size() << " wheel slots\n";
    }
    return failed ? 1 : 0;
}
