#include "runtime/ParquetExporter.h"

#include <cmath>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>

#ifdef POKER_ENABLE_PARQUET_EXPORT
#include <arrow/api.h>
#include <arrow/io/file.h>
#include <parquet/arrow/writer.h>
#include <parquet/properties.h>
#endif

namespace {
constexpr int kExportFloatPrecision = 3;

double roundToPrecision(double value, int float_precision = kExportFloatPrecision) {
    const double multiplier = std::pow(10.0, float_precision);
    return std::round(value * multiplier) / multiplier;
}

void writeJsonWithPrecision(std::ostream& os, const json& j, int float_precision = kExportFloatPrecision) {
    if (j.is_null()) {
        os << "null";
    } else if (j.is_boolean()) {
        os << (j.get<bool>() ? "true" : "false");
    } else if (j.is_number_integer()) {
        os << j.get<int64_t>();
    } else if (j.is_number_unsigned()) {
        os << j.get<uint64_t>();
    } else if (j.is_number_float()) {
        const double val = j.get<double>();
        const double rounded = roundToPrecision(val, float_precision);
        if (rounded == static_cast<int64_t>(rounded)) {
            os << static_cast<int64_t>(rounded);
        } else {
            os << std::fixed << std::setprecision(float_precision) << rounded;
        }
    } else if (j.is_string()) {
        os << "\"" << j.get<std::string>() << "\"";
    } else if (j.is_array()) {
        os << "[";
        bool first = true;
        for (const auto& item : j) {
            if (!first) {
                os << ",";
            }
            first = false;
            writeJsonWithPrecision(os, item, float_precision);
        }
        os << "]";
    } else if (j.is_object()) {
        os << "{";
        bool first = true;
        for (auto it = j.begin(); it != j.end(); ++it) {
            if (!first) {
                os << ",";
            }
            first = false;
            os << "\"" << it.key() << "\":";
            writeJsonWithPrecision(os, it.value(), float_precision);
        }
        os << "}";
    }
}

std::string jsonToCompactString(const json& dump_json) {
    std::ostringstream os;
    writeJsonWithPrecision(os, dump_json, kExportFloatPrecision);
    return os.str();
}
}

#ifndef POKER_ENABLE_PARQUET_EXPORT

void writeSolverParquetJson(const std::string&, const json&, int) {
    throw std::runtime_error("parquet export support is disabled in this build; rebuild with ENABLE_PARQUET_EXPORT=ON");
}

void writeStructuredSolverParquet(const std::string&, const json&, int) {
    throw std::runtime_error("structured parquet export support is disabled in this build; rebuild with ENABLE_PARQUET_EXPORT=ON");
}

#else

namespace {
template <typename T>
T unwrapArrowResult(arrow::Result<T> result, const std::string& context) {
    if (!result.ok()) {
        throw std::runtime_error(context + ": " + result.status().ToString());
    }
    return result.MoveValueUnsafe();
}

void requireArrowOk(const arrow::Status& status, const std::string& context) {
    if (!status.ok()) {
        throw std::runtime_error(context + ": " + status.ToString());
    }
}

std::shared_ptr<arrow::KeyValueMetadata> buildMetadata(const std::string& export_format, int dump_rounds) {
    auto metadata = std::make_shared<arrow::KeyValueMetadata>();
    metadata->Append("solver_export_format", export_format);
    metadata->Append("solver_schema_version", "1");
    metadata->Append("dump_rounds", std::to_string(dump_rounds));
    return metadata;
}

void writeParquetTable(
        const std::string& output_file,
        const std::shared_ptr<arrow::Table>& table,
        parquet::Compression::type compression
) {
    auto sink = unwrapArrowResult(arrow::io::FileOutputStream::Open(output_file), "open parquet output");
    parquet::WriterProperties::Builder writer_builder;
    writer_builder.compression(compression);
    const auto writer_properties = writer_builder.build();
    requireArrowOk(
            parquet::arrow::WriteTable(*table, arrow::default_memory_pool(), sink, 1024, writer_properties),
            "write parquet table"
    );
    requireArrowOk(sink->Close(), "close parquet output");
}

struct StructuredRow {
    int64_t node_id = 0;
    bool has_parent = false;
    int64_t parent_node_id = 0;
    int32_t depth = 0;
    std::string node_type;
    bool has_edge_label = false;
    std::string edge_label;
    bool has_player = false;
    int32_t player = 0;
    bool has_deal_number = false;
    int32_t deal_number = 0;
    bool has_deal_card = false;
    std::string deal_card;
    bool reach_probs_only = false;
    bool has_actions = false;
    std::vector<std::string> actions;
    bool has_hand = false;
    std::string hand;
    bool has_strategy_probs = false;
    std::vector<double> strategy_probs;
    bool has_evs = false;
    std::vector<double> evs;
    bool has_equities = false;
    std::vector<double> equities;
    bool has_ip_range = false;
    double ip_range = 0.0;
    bool has_oop_range = false;
    double oop_range = 0.0;
};

std::vector<std::string> jsonArrayToStrings(const json& value) {
    std::vector<std::string> result;
    if (!value.is_array()) {
        return result;
    }
    result.reserve(value.size());
    for (const auto& item : value) {
        result.push_back(item.get<std::string>());
    }
    return result;
}

std::vector<double> jsonArrayToRoundedDoubles(const json& value) {
    std::vector<double> result;
    if (!value.is_array()) {
        return result;
    }
    result.reserve(value.size());
    for (const auto& item : value) {
        result.push_back(roundToPrecision(item.get<double>()));
    }
    return result;
}

void addHandsFromMap(const json& container, const char* key, std::set<std::string>& hands) {
    if (!container.is_object() || !container.contains(key) || !container[key].is_object()) {
        return;
    }
    for (auto it = container[key].begin(); it != container[key].end(); ++it) {
        hands.insert(it.key());
    }
}

bool hasFloatValue(const json& container, const char* key, const std::string& hand) {
    return container.is_object() && container.contains(key) && container[key].is_object() && container[key].contains(hand);
}

double getRoundedFloatValue(const json& container, const char* key, const std::string& hand) {
    return roundToPrecision(container[key][hand].get<double>());
}

bool hasVectorValue(const json& container, const char* key, const std::string& hand) {
    return container.is_object() && container.contains(key) && container[key].is_object() && container[key].contains(hand) &&
           container[key][hand].is_array();
}

std::vector<double> getVectorValue(const json& container, const char* key, const std::string& hand) {
    return jsonArrayToRoundedDoubles(container[key][hand]);
}

class StructuredFlattener {
public:
    std::vector<StructuredRow> flatten(const json& root) {
        rows.clear();
        next_node_id = 0;
        visit(root, false, 0, "", 0);
        return rows;
    }

private:
    std::vector<StructuredRow> rows;
    int64_t next_node_id = 0;

    int64_t visit(const json& node, bool has_parent, int64_t parent_node_id, const std::string& edge_label, int depth) {
        const int64_t node_id = next_node_id++;
        const std::string node_type = node.value("node_type", "unknown");
        if (node_type == "action_node") {
            emitActionRows(node, node_id, has_parent, parent_node_id, edge_label, depth);
            if (node.contains("childrens") && node["childrens"].is_object()) {
                for (auto it = node["childrens"].begin(); it != node["childrens"].end(); ++it) {
                    visit(it.value(), true, node_id, it.key(), depth + 1);
                }
            }
        } else if (node_type == "chance_node") {
            StructuredRow row;
            row.node_id = node_id;
            row.has_parent = has_parent;
            row.parent_node_id = parent_node_id;
            row.depth = depth;
            row.node_type = node_type;
            row.has_edge_label = has_parent;
            row.edge_label = edge_label;
            row.has_deal_number = node.contains("deal_number");
            if (row.has_deal_number) {
                row.deal_number = node["deal_number"].get<int32_t>();
            }
            row.has_deal_card = has_parent;
            row.deal_card = edge_label;
            rows.push_back(std::move(row));
            if (node.contains("dealcards") && node["dealcards"].is_object()) {
                for (auto it = node["dealcards"].begin(); it != node["dealcards"].end(); ++it) {
                    visit(it.value(), true, node_id, it.key(), depth + 1);
                }
            }
        }
        return node_id;
    }

    void emitActionRows(
            const json& node,
            int64_t node_id,
            bool has_parent,
            int64_t parent_node_id,
            const std::string& edge_label,
            int depth
    ) {
        std::set<std::string> hands;
        if (node.contains("strategy")) {
            addHandsFromMap(node["strategy"], "strategy", hands);
        }
        if (node.contains("evs")) {
            addHandsFromMap(node["evs"], "evs", hands);
        }
        if (node.contains("equities")) {
            addHandsFromMap(node["equities"], "equities", hands);
        }
        if (node.contains("ranges")) {
            addHandsFromMap(node["ranges"], "ip_range", hands);
            addHandsFromMap(node["ranges"], "oop_range", hands);
        }

        const std::vector<std::string> actions = jsonArrayToStrings(node.value("actions", json::array()));
        if (hands.empty()) {
            StructuredRow row;
            row.node_id = node_id;
            row.has_parent = has_parent;
            row.parent_node_id = parent_node_id;
            row.depth = depth;
            row.node_type = "action_node";
            row.has_edge_label = has_parent;
            row.edge_label = edge_label;
            row.has_player = node.contains("player");
            if (row.has_player) {
                row.player = node["player"].get<int32_t>();
            }
            row.reach_probs_only = node.value("reach_probs_only", false);
            row.has_actions = !actions.empty();
            row.actions = actions;
            rows.push_back(std::move(row));
            return;
        }

        for (const auto& hand : hands) {
            StructuredRow row;
            row.node_id = node_id;
            row.has_parent = has_parent;
            row.parent_node_id = parent_node_id;
            row.depth = depth;
            row.node_type = "action_node";
            row.has_edge_label = has_parent;
            row.edge_label = edge_label;
            row.has_player = node.contains("player");
            if (row.has_player) {
                row.player = node["player"].get<int32_t>();
            }
            row.reach_probs_only = node.value("reach_probs_only", false);
            row.has_actions = !actions.empty();
            row.actions = actions;
            row.has_hand = true;
            row.hand = hand;

            if (node.contains("strategy") && hasVectorValue(node["strategy"], "strategy", hand)) {
                row.has_strategy_probs = true;
                row.strategy_probs = getVectorValue(node["strategy"], "strategy", hand);
            }
            if (node.contains("evs") && hasVectorValue(node["evs"], "evs", hand)) {
                row.has_evs = true;
                row.evs = getVectorValue(node["evs"], "evs", hand);
            }
            if (node.contains("equities") && hasVectorValue(node["equities"], "equities", hand)) {
                row.has_equities = true;
                row.equities = getVectorValue(node["equities"], "equities", hand);
            }
            if (node.contains("ranges") && hasFloatValue(node["ranges"], "ip_range", hand)) {
                row.has_ip_range = true;
                row.ip_range = getRoundedFloatValue(node["ranges"], "ip_range", hand);
            }
            if (node.contains("ranges") && hasFloatValue(node["ranges"], "oop_range", hand)) {
                row.has_oop_range = true;
                row.oop_range = getRoundedFloatValue(node["ranges"], "oop_range", hand);
            }
            rows.push_back(std::move(row));
        }
    }
};

class StructuredRowBuilders {
public:
    StructuredRowBuilders()
            : actions_builder(arrow::default_memory_pool(), std::make_shared<arrow::StringBuilder>()),
              strategy_builder(arrow::default_memory_pool(), std::make_shared<arrow::DoubleBuilder>()),
              evs_builder(arrow::default_memory_pool(), std::make_shared<arrow::DoubleBuilder>()),
              equities_builder(arrow::default_memory_pool(), std::make_shared<arrow::DoubleBuilder>()) {
    }

    void append(const StructuredRow& row) {
        requireArrowOk(node_id_builder.Append(row.node_id), "append node_id");
        if (row.has_parent) {
            requireArrowOk(parent_node_id_builder.Append(row.parent_node_id), "append parent_node_id");
        } else {
            requireArrowOk(parent_node_id_builder.AppendNull(), "append parent_node_id null");
        }
        requireArrowOk(depth_builder.Append(row.depth), "append depth");
        requireArrowOk(node_type_builder.Append(row.node_type), "append node_type");
        if (row.has_edge_label) {
            requireArrowOk(edge_label_builder.Append(row.edge_label), "append edge_label");
        } else {
            requireArrowOk(edge_label_builder.AppendNull(), "append edge_label null");
        }
        if (row.has_player) {
            requireArrowOk(player_builder.Append(row.player), "append player");
        } else {
            requireArrowOk(player_builder.AppendNull(), "append player null");
        }
        if (row.has_deal_number) {
            requireArrowOk(deal_number_builder.Append(row.deal_number), "append deal_number");
        } else {
            requireArrowOk(deal_number_builder.AppendNull(), "append deal_number null");
        }
        if (row.has_deal_card) {
            requireArrowOk(deal_card_builder.Append(row.deal_card), "append deal_card");
        } else {
            requireArrowOk(deal_card_builder.AppendNull(), "append deal_card null");
        }
        requireArrowOk(reach_probs_only_builder.Append(row.reach_probs_only), "append reach_probs_only");

        if (row.has_actions) {
            requireArrowOk(actions_builder.Append(), "append actions list");
            auto* values = static_cast<arrow::StringBuilder*>(actions_builder.value_builder());
            for (const auto& action : row.actions) {
                requireArrowOk(values->Append(action), "append action");
            }
        } else {
            requireArrowOk(actions_builder.AppendNull(), "append actions null");
        }

        if (row.has_hand) {
            requireArrowOk(hand_builder.Append(row.hand), "append hand");
        } else {
            requireArrowOk(hand_builder.AppendNull(), "append hand null");
        }

        appendFloatList(row.has_strategy_probs, row.strategy_probs, strategy_builder, "strategy_probs");
        appendFloatList(row.has_evs, row.evs, evs_builder, "evs");
        appendFloatList(row.has_equities, row.equities, equities_builder, "equities");

        if (row.has_ip_range) {
            requireArrowOk(ip_range_builder.Append(row.ip_range), "append ip_range");
        } else {
            requireArrowOk(ip_range_builder.AppendNull(), "append ip_range null");
        }
        if (row.has_oop_range) {
            requireArrowOk(oop_range_builder.Append(row.oop_range), "append oop_range");
        } else {
            requireArrowOk(oop_range_builder.AppendNull(), "append oop_range null");
        }
    }

    std::shared_ptr<arrow::Table> finish(int dump_rounds) {
        std::vector<std::shared_ptr<arrow::Array>> arrays;
        arrays.push_back(finishArray(node_id_builder, "node_id"));
        arrays.push_back(finishArray(parent_node_id_builder, "parent_node_id"));
        arrays.push_back(finishArray(depth_builder, "depth"));
        arrays.push_back(finishArray(node_type_builder, "node_type"));
        arrays.push_back(finishArray(edge_label_builder, "edge_label"));
        arrays.push_back(finishArray(player_builder, "player"));
        arrays.push_back(finishArray(deal_number_builder, "deal_number"));
        arrays.push_back(finishArray(deal_card_builder, "deal_card"));
        arrays.push_back(finishArray(reach_probs_only_builder, "reach_probs_only"));
        arrays.push_back(finishArray(actions_builder, "actions"));
        arrays.push_back(finishArray(hand_builder, "hand"));
        arrays.push_back(finishArray(strategy_builder, "strategy_probs"));
        arrays.push_back(finishArray(evs_builder, "evs"));
        arrays.push_back(finishArray(equities_builder, "equities"));
        arrays.push_back(finishArray(ip_range_builder, "ip_range"));
        arrays.push_back(finishArray(oop_range_builder, "oop_range"));

        auto schema = arrow::schema(
                {
                        arrow::field("node_id", arrow::int64(), false),
                        arrow::field("parent_node_id", arrow::int64(), true),
                        arrow::field("depth", arrow::int32(), false),
                        arrow::field("node_type", arrow::utf8(), false),
                        arrow::field("edge_label", arrow::utf8(), true),
                        arrow::field("player", arrow::int32(), true),
                        arrow::field("deal_number", arrow::int32(), true),
                        arrow::field("deal_card", arrow::utf8(), true),
                        arrow::field("reach_probs_only", arrow::boolean(), false),
                        arrow::field("actions", arrow::list(arrow::utf8()), true),
                        arrow::field("hand", arrow::utf8(), true),
                        arrow::field("strategy_probs", arrow::list(arrow::float64()), true),
                        arrow::field("evs", arrow::list(arrow::float64()), true),
                        arrow::field("equities", arrow::list(arrow::float64()), true),
                        arrow::field("ip_range", arrow::float64(), true),
                        arrow::field("oop_range", arrow::float64(), true),
                },
                buildMetadata("parquet_native", dump_rounds)
        );
        return arrow::Table::Make(schema, arrays);
    }

private:
    arrow::Int64Builder node_id_builder;
    arrow::Int64Builder parent_node_id_builder;
    arrow::Int32Builder depth_builder;
    arrow::StringBuilder node_type_builder;
    arrow::StringBuilder edge_label_builder;
    arrow::Int32Builder player_builder;
    arrow::Int32Builder deal_number_builder;
    arrow::StringBuilder deal_card_builder;
    arrow::BooleanBuilder reach_probs_only_builder;
    arrow::ListBuilder actions_builder;
    arrow::StringBuilder hand_builder;
    arrow::ListBuilder strategy_builder;
    arrow::ListBuilder evs_builder;
    arrow::ListBuilder equities_builder;
    arrow::DoubleBuilder ip_range_builder;
    arrow::DoubleBuilder oop_range_builder;

    template <typename BuilderType>
    std::shared_ptr<arrow::Array> finishArray(BuilderType& builder, const std::string& name) {
        std::shared_ptr<arrow::Array> array;
        requireArrowOk(builder.Finish(&array), "finish " + name);
        return array;
    }

    void appendFloatList(bool has_value, const std::vector<double>& values, arrow::ListBuilder& builder, const std::string& name) {
        if (!has_value) {
            requireArrowOk(builder.AppendNull(), "append " + name + " null");
            return;
        }
        requireArrowOk(builder.Append(), "append " + name + " list");
        auto* value_builder = static_cast<arrow::DoubleBuilder*>(builder.value_builder());
        for (double value : values) {
            requireArrowOk(value_builder->Append(value), "append " + name + " value");
        }
    }
};
}

void writeSolverParquetJson(const std::string& output_file, const json& dump_json, int dump_rounds) {
    arrow::StringBuilder data_builder;
    requireArrowOk(data_builder.Append(jsonToCompactString(dump_json)), "append data");

    std::shared_ptr<arrow::Array> data_array;
    requireArrowOk(data_builder.Finish(&data_array), "finish data");

    auto schema = arrow::schema(
            {arrow::field("data", arrow::utf8(), false)},
            buildMetadata("parquet", dump_rounds)
    );
    const auto table = arrow::Table::Make(schema, {data_array});
    writeParquetTable(output_file, table, parquet::Compression::SNAPPY);
}

void writeStructuredSolverParquet(const std::string& output_file, const json& dump_json, int dump_rounds) {
#ifndef POKER_ENABLE_STRUCTURED_PARQUET
    (void)output_file;
    (void)dump_json;
    (void)dump_rounds;
    throw std::runtime_error("structured parquet export is only enabled on Linux builds");
#else
    StructuredFlattener flattener;
    const auto rows = flattener.flatten(dump_json);
    StructuredRowBuilders builders;
    for (const auto& row : rows) {
        builders.append(row);
    }
    const auto table = builders.finish(dump_rounds);
    writeParquetTable(output_file, table, parquet::Compression::ZSTD);
#endif
}

#endif
