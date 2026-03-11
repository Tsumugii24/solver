#ifndef TEXASSOLVER_EXPORTFORMAT_H
#define TEXASSOLVER_EXPORTFORMAT_H

#include <stdexcept>
#include <string>

enum class SolverDumpFormat {
    JSON,
    PARQUET_JSON,
    PARQUET_STRUCTURED
};

SolverDumpFormat parseSolverDumpFormat(const std::string& value);
const char* solverDumpFormatName(SolverDumpFormat format);
bool solverDumpFormatRequiresParquet(SolverDumpFormat format);
const char* solverDumpFormatRecommendedExtension(SolverDumpFormat format);
bool solverDumpFormatSupportedOnCurrentPlatform(SolverDumpFormat format);
const char* solverDumpFormatPlatformSupportMessage(SolverDumpFormat format);

#endif //TEXASSOLVER_EXPORTFORMAT_H
