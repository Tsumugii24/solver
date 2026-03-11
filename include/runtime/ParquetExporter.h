#ifndef TEXASSOLVER_PARQUETEXPORTER_H
#define TEXASSOLVER_PARQUETEXPORTER_H

#include <string>

#include "json.hpp"

using json = nlohmann::json;

void writeSolverParquetJson(const std::string& output_file, const json& dump_json, int dump_rounds);
void writeStructuredSolverParquet(const std::string& output_file, const json& dump_json, int dump_rounds);

#endif //TEXASSOLVER_PARQUETEXPORTER_H
