#include "runtime/ExportFormat.h"

#include <algorithm>
#include <cctype>

namespace {
std::string normalizeFormat(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        if (c == '-') {
            return '_';
        }
        return static_cast<char>(std::tolower(c));
    });
    return value;
}
}

SolverDumpFormat parseSolverDumpFormat(const std::string& value) {
    const std::string normalized = normalizeFormat(value);
    if (normalized.empty() || normalized == "json") {
        return SolverDumpFormat::JSON;
    }
    if (normalized == "parquet" || normalized == "parquet_json") {
        return SolverDumpFormat::PARQUET_JSON;
    }
    if (normalized == "parquet_native" || normalized == "parquet_structured") {
        return SolverDumpFormat::PARQUET_STRUCTURED;
    }
    throw std::runtime_error("unknown dump format: " + value);
}

const char* solverDumpFormatName(SolverDumpFormat format) {
    switch (format) {
        case SolverDumpFormat::JSON:
            return "json";
        case SolverDumpFormat::PARQUET_JSON:
            return "parquet";
        case SolverDumpFormat::PARQUET_STRUCTURED:
            return "parquet_native";
    }
    return "json";
}

bool solverDumpFormatRequiresParquet(SolverDumpFormat format) {
    return format != SolverDumpFormat::JSON;
}

const char* solverDumpFormatRecommendedExtension(SolverDumpFormat format) {
    return solverDumpFormatRequiresParquet(format) ? ".parquet" : ".json";
}

bool solverDumpFormatSupportedOnCurrentPlatform(SolverDumpFormat format) {
#ifdef _WIN32
    return format != SolverDumpFormat::PARQUET_STRUCTURED;
#else
    return true;
#endif
}

const char* solverDumpFormatPlatformSupportMessage(SolverDumpFormat format) {
#ifdef _WIN32
    if (format == SolverDumpFormat::PARQUET_STRUCTURED) {
        return "parquet_native is only supported on Linux builds";
    }
#endif
    (void)format;
    return "";
}
