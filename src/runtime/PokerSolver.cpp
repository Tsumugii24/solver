//
// Created by Xuefeng Huang on 2020/2/6.
//

#include "runtime/PokerSolver.h"
#include "runtime/ParquetExporter.h"
#include "solver/BestResponse.h"
#include <algorithm>
#include <iomanip>
#include <chrono>
#include <sstream>
#include <cmath>

// 自定义 JSON 序列化函数，控制浮点数精度
namespace {

constexpr uint64_t kBytesPerMiB = 1024ull * 1024ull;
constexpr int kPokerPlayerCount = 2;
constexpr int kPrivateCardsDynamicInts = 2;

uint64_t choose_count(int n, int k) {
    if (k < 0 || n < k) {
        return 0;
    }
    if (k == 0 || k == n) {
        return 1;
    }

    k = std::min(k, n - k);
    uint64_t result = 1;
    for (int i = 1; i <= k; ++i) {
        result = result * static_cast<uint64_t>(n - k + i) / static_cast<uint64_t>(i);
    }
    return result;
}

uint64_t ordered_deal_count(int remaining_deck_size, int gap) {
    if (gap <= 0) {
        return 1;
    }

    uint64_t result = 1;
    for (int i = 0; i < gap; ++i) {
        if (remaining_deck_size - i <= 0) {
            return 0;
        }
        result *= static_cast<uint64_t>(remaining_deck_size - i);
    }
    return result;
}

uint64_t deal_slot_capacity(int full_deck_size, int gap) {
    uint64_t total = 1;
    uint64_t power = 1;
    for (int i = 1; i <= gap; ++i) {
        power *= static_cast<uint64_t>(full_deck_size);
        total += power;
    }
    return total;
}

uint64_t bytes_from_floats(uint64_t float_count) {
    return float_count * sizeof(float);
}

uint64_t estimate_private_cards_vector_bytes(size_t combo_count) {
    return static_cast<uint64_t>(combo_count) *
           (sizeof(PrivateCards) + kPrivateCardsDynamicInts * sizeof(int));
}

uint64_t estimate_discounted_trainable_bytes(size_t action_count, size_t combo_count) {
    const uint64_t float_count =
            static_cast<uint64_t>(combo_count) * (4ull * static_cast<uint64_t>(action_count) + 1ull);
    return sizeof(DiscountedCfrTrainable) + bytes_from_floats(float_count);
}

uint64_t estimate_river_comb_bytes(int final_board_cards) {
    return sizeof(RiverCombs) +
           static_cast<uint64_t>(final_board_cards + kPrivateCardsDynamicInts) * sizeof(int);
}

double survival_probability_after_future_cards(int remaining_deck_size, int future_board_cards) {
    if (future_board_cards <= 0) {
        return 1.0;
    }
    if (remaining_deck_size < future_board_cards || remaining_deck_size < kPrivateCardsDynamicInts) {
        return 0.0;
    }

    const uint64_t total = choose_count(remaining_deck_size, future_board_cards);
    const uint64_t safe = choose_count(remaining_deck_size - kPrivateCardsDynamicInts, future_board_cards);
    if (total == 0) {
        return 0.0;
    }
    return static_cast<double>(safe) / static_cast<double>(total);
}

int round_gap(GameTreeNode::GameRound root_round, GameTreeNode::GameRound node_round) {
    return GameTreeNode::gameRound2int(node_round) - GameTreeNode::gameRound2int(root_round);
}

struct TreeEstimateAccumulator {
    uint64_t tree_bytes = 0;
    uint64_t trainable_slot_bytes = 0;
    uint64_t trainable_data_bytes = 0;
    uint64_t action_node_count = 0;
    uint64_t chance_node_count = 0;
    uint64_t max_actions_per_node = 0;
};

void accumulate_tree_estimate(
        const shared_ptr<GameTreeNode>& node,
        GameTreeNode::GameRound root_round,
        int full_deck_size,
        int remaining_deck_size,
        size_t p1_range_size,
        size_t p2_range_size,
        TreeEstimateAccumulator& acc
) {
    if (node == nullptr) {
        return;
    }

    switch (node->getType()) {
        case GameTreeNode::ACTION: {
            shared_ptr<ActionNode> action_node = std::dynamic_pointer_cast<ActionNode>(node);
            vector<shared_ptr<GameTreeNode>>& children = action_node->getChildrens();
            vector<GameActions>& actions = action_node->getActions();

            acc.action_node_count += 1;
            acc.max_actions_per_node = std::max<uint64_t>(acc.max_actions_per_node, actions.size());
            acc.tree_bytes += sizeof(ActionNode);
            acc.tree_bytes += static_cast<uint64_t>(children.capacity()) * sizeof(shared_ptr<GameTreeNode>);
            acc.tree_bytes += static_cast<uint64_t>(actions.capacity()) * sizeof(GameActions);

            const int gap = std::max(0, round_gap(root_round, action_node->getRound()));
            const size_t combo_count = action_node->getPlayer() == 0 ? p1_range_size : p2_range_size;
            acc.trainable_slot_bytes += deal_slot_capacity(full_deck_size, gap) * sizeof(shared_ptr<Trainable>);
            acc.trainable_data_bytes += ordered_deal_count(remaining_deck_size, gap) *
                                        estimate_discounted_trainable_bytes(actions.size(), combo_count);

            for (const shared_ptr<GameTreeNode>& child : children) {
                accumulate_tree_estimate(
                        child,
                        root_round,
                        full_deck_size,
                        remaining_deck_size,
                        p1_range_size,
                        p2_range_size,
                        acc
                );
            }
            break;
        }
        case GameTreeNode::CHANCE: {
            shared_ptr<ChanceNode> chance_node = std::dynamic_pointer_cast<ChanceNode>(node);
            acc.chance_node_count += 1;
            acc.tree_bytes += sizeof(ChanceNode);
            accumulate_tree_estimate(
                    chance_node->getChildren(),
                    root_round,
                    full_deck_size,
                    remaining_deck_size,
                    p1_range_size,
                    p2_range_size,
                    acc
            );
            break;
        }
        case GameTreeNode::TERMINAL: {
            shared_ptr<TerminalNode> terminal_node = std::dynamic_pointer_cast<TerminalNode>(node);
            acc.tree_bytes += sizeof(TerminalNode);
            acc.tree_bytes += static_cast<uint64_t>(terminal_node->get_payoffs().size()) * sizeof(double);
            break;
        }
        case GameTreeNode::SHOWDOWN: {
            shared_ptr<ShowdownNode> showdown_node = std::dynamic_pointer_cast<ShowdownNode>(node);
            acc.tree_bytes += sizeof(ShowdownNode);
            acc.tree_bytes += static_cast<uint64_t>(showdown_node->get_payoffs(ShowdownNode::TIE, -1).size()) * sizeof(double);
            acc.tree_bytes += 2ull * sizeof(vector<double>);
            acc.tree_bytes += static_cast<uint64_t>(showdown_node->get_payoffs(ShowdownNode::NOTTIE, 0).size()) * sizeof(double);
            acc.tree_bytes += static_cast<uint64_t>(showdown_node->get_payoffs(ShowdownNode::NOTTIE, 1).size()) * sizeof(double);
            break;
        }
    }
}

} // namespace

static void write_json_with_precision(std::ostream& os, const json& j, int float_precision = 3, int indent = 0, bool pretty = false) {
    const std::string indent_str = pretty ? std::string(indent * 2, ' ') : "";
    const std::string newline = pretty ? "\n" : "";
    
    if (j.is_null()) {
        os << "null";
    } else if (j.is_boolean()) {
        os << (j.get<bool>() ? "true" : "false");
    } else if (j.is_number_integer()) {
        os << j.get<int64_t>();
    } else if (j.is_number_unsigned()) {
        os << j.get<uint64_t>();
    } else if (j.is_number_float()) {
        // 关键：控制浮点数精度，整数则不带小数点
        double val = j.get<double>();
        // 先四舍五入到指定精度
        double multiplier = std::pow(10.0, float_precision);
        double rounded = std::round(val * multiplier) / multiplier;
        // 判断是否为整数
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
            if (!first) os << ",";
            first = false;
            write_json_with_precision(os, item, float_precision, indent + 1, pretty);
        }
        os << "]";
    } else if (j.is_object()) {
        os << "{" << newline;
        bool first = true;
        for (auto it = j.begin(); it != j.end(); ++it) {
            if (!first) os << "," << newline;
            first = false;
            if (pretty) os << indent_str << "  ";
            os << "\"" << it.key() << "\":";
            write_json_with_precision(os, it.value(), float_precision, indent + 1, pretty);
        }
        os << newline;
        if (pretty) os << indent_str;
        os << "}";
    }
}

PokerSolver::PokerSolver() {

}

PokerSolver::PokerSolver(string ranks, string suits, string compairer_file,int compairer_file_lines, string compairer_file_bin) {
    vector<string> ranks_vector = string_split(ranks,',');
    vector<string> suits_vector = string_split(suits,',');
    this->deck = Deck(ranks_vector,suits_vector);
    this->compairer = make_shared<Dic5Compairer>(compairer_file,compairer_file_lines,compairer_file_bin);
}

void PokerSolver::load_game_tree(string game_tree_file) {
    shared_ptr<GameTree> game_tree = make_shared<GameTree>(game_tree_file,this->deck);
    this->game_tree = game_tree;
}

void PokerSolver::build_game_tree(
        float oop_commit,
        float ip_commit,
        int current_round,
        int raise_limit,
        float small_blind,
        float big_blind,
        float stack,
        GameTreeBuildingSettings buildingSettings,
        float allin_threshold
){

    shared_ptr<GameTree> game_tree = make_shared<GameTree>(
            this->deck,
            oop_commit,
            ip_commit,
            current_round,
            raise_limit,
            small_blind,
            big_blind,
            stack,
            buildingSettings,
            allin_threshold
    );
    this->game_tree = game_tree;
}

void PokerSolver::train(string p1_range, string p2_range, string boards, string log_file, int iteration_number,
                        int print_interval, string algorithm,int warmup,float accuracy,bool use_isomorphism,int threads,bool enable_equity,bool enable_range) {
    string player1RangeStr = p1_range;
    string player2RangeStr = p2_range;

    vector<string> board_str_arr = string_split(boards,',');
    vector<int> initialBoard;
    for(string one_board_str:board_str_arr){
        initialBoard.push_back(Card::strCard2int(one_board_str));
    }

    vector<PrivateCards> player1Range = PrivateRangeConverter::rangeStr2Cards(player1RangeStr,initialBoard);
    vector<PrivateCards> player2Range = PrivateRangeConverter::rangeStr2Cards(player2RangeStr,initialBoard);
    string logfile_name = log_file;
    this->solver = make_shared<PCfrSolver>(
            game_tree
            , player1Range
            , player2Range
            , initialBoard
            , compairer
            , deck
            , iteration_number
            , false
            , print_interval
            , logfile_name
            , algorithm
            , Solver::MonteCarolAlg::NONE
            , warmup
            , accuracy
            , use_isomorphism
            , threads
            , enable_equity
            , enable_range
    );
    this->solver->train();
}

void PokerSolver::dump_strategy(string dump_file,int dump_rounds,SolverDumpFormat dump_format) {
    auto start_time = std::chrono::high_resolution_clock::now();
    
    // dumps 函数内部会显示进度条
    json dump_json = this->solver->dumps(false,dump_rounds);
    
    auto gen_time = std::chrono::high_resolution_clock::now();
    auto gen_duration = std::chrono::duration_cast<std::chrono::milliseconds>(gen_time - start_time).count();
    cout << "Generation time: " << gen_duration / 1000.0 << "s" << endl;
    
    cout << "Writing " << solverDumpFormatName(dump_format) << " to file: " << dump_file << "..." << flush;
    if(dump_format == SolverDumpFormat::JSON) {
        ofstream fileWriter;
        fileWriter.open(dump_file);
        // 使用自定义序列化函数，确保浮点数精度为3位小数
        write_json_with_precision(fileWriter, dump_json, 3, 0, false);
        fileWriter.flush();
        fileWriter.close();
    } else if(dump_format == SolverDumpFormat::PARQUET_JSON) {
        writeSolverParquetJson(dump_file, dump_json, dump_rounds);
    } else if(dump_format == SolverDumpFormat::PARQUET_STRUCTURED) {
        writeStructuredSolverParquet(dump_file, dump_json, dump_rounds);
    } else {
        throw runtime_error("unsupported dump format");
    }
    auto write_time = std::chrono::high_resolution_clock::now();
    auto write_duration = std::chrono::duration_cast<std::chrono::milliseconds>(write_time - gen_time).count();
    cout << " done (" << write_duration / 1000.0 << "s)" << endl;
}

const shared_ptr<GameTree> &PokerSolver::getGameTree() const {
    return game_tree;
}

long long PokerSolver::estimate_tree_memory(string p1_range, string p2_range, string boards) {
    SolverMemoryEstimate estimate = this->estimate_memory_details(std::move(p1_range), std::move(p2_range), std::move(boards));
    return static_cast<long long>(estimate.practical_peak_bytes() / sizeof(float));
}

SolverMemoryEstimate PokerSolver::estimate_memory_details(string p1_range, string p2_range, string boards) {
    SolverMemoryEstimate estimate;
    if(this->game_tree == nullptr){
        cout << "Please build tree first." << endl;
        return estimate;
    }

    vector<string> board_str_arr = string_split(boards,',');
    vector<int> initialBoard;
    for(const string& one_board_str: board_str_arr){
        if(one_board_str.empty()){
            continue;
        }
        initialBoard.push_back(Card::strCard2int(one_board_str));
    }

    vector<PrivateCards> range1 = PrivateRangeConverter::rangeStr2Cards(p1_range,initialBoard);
    vector<PrivateCards> range2 = PrivateRangeConverter::rangeStr2Cards(p2_range,initialBoard);

    const int full_deck_size = static_cast<int>(this->deck.getCards().size());
    const int remaining_deck_size = std::max(0, full_deck_size - static_cast<int>(initialBoard.size()));
    const shared_ptr<GameTree> current_tree = this->game_tree;
    const shared_ptr<GameTreeNode> root = current_tree->getRoot();
    const GameTreeNode::GameRound root_round = root->getRound();
    estimate.available = true;
    estimate.original_texassolver_heuristic_bytes = bytes_from_floats(
            current_tree->estimate_tree_memory(
                    remaining_deck_size,
                    static_cast<int>(range1.size()),
                    static_cast<int>(range2.size())
            )
    );

    TreeEstimateAccumulator acc;
    acc.tree_bytes += sizeof(GameTree);
    accumulate_tree_estimate(
            root,
            root_round,
            full_deck_size,
            remaining_deck_size,
            range1.size(),
            range2.size(),
            acc
    );

    estimate.tree_bytes = acc.tree_bytes;
    estimate.trainable_slot_bytes = acc.trainable_slot_bytes;
    estimate.trainable_data_bytes = acc.trainable_data_bytes;
    estimate.action_node_count = acc.action_node_count;
    estimate.chance_node_count = acc.chance_node_count;
    estimate.max_actions_per_node = acc.max_actions_per_node;

    const uint64_t range_storage_bytes =
            3ull * (estimate_private_cards_vector_bytes(range1.size()) + estimate_private_cards_vector_bytes(range2.size()));
    const uint64_t private_cards_index_bytes =
            52ull * 52ull * sizeof(vector<int>) +
            52ull * 52ull * kPokerPlayerCount * sizeof(int);
    const uint64_t reach_prob_bytes = bytes_from_floats(static_cast<uint64_t>(range1.size() + range2.size()));
    estimate.solver_state_bytes = sizeof(PCfrSolver) + range_storage_bytes + private_cards_index_bytes + reach_prob_bytes;

    const int future_board_cards = std::max(0, 5 - static_cast<int>(initialBoard.size()));
    const uint64_t unique_river_boards = future_board_cards == 0
                                         ? 1ull
                                         : choose_count(remaining_deck_size, future_board_cards);
    const double p1_survival_prob = survival_probability_after_future_cards(remaining_deck_size, future_board_cards);
    const double p2_survival_prob = survival_probability_after_future_cards(remaining_deck_size, future_board_cards);
    const int final_board_cards = static_cast<int>(initialBoard.size()) + future_board_cards;
    const double avg_p1_river_combos = static_cast<double>(range1.size()) * p1_survival_prob;
    const double avg_p2_river_combos = static_cast<double>(range2.size()) * p2_survival_prob;
    const double per_board_cache_bytes =
            2.0 * sizeof(vector<RiverCombs>) +
            avg_p1_river_combos * static_cast<double>(estimate_river_comb_bytes(final_board_cards)) +
            avg_p2_river_combos * static_cast<double>(estimate_river_comb_bytes(final_board_cards)) +
            2.0 * (sizeof(uint64_t) + sizeof(vector<RiverCombs>) + 4.0 * sizeof(void*));
    estimate.river_cache_bytes = static_cast<uint64_t>(std::llround(static_cast<double>(unique_river_boards) * per_board_cache_bytes));

    const uint64_t max_range_size = std::max<uint64_t>(range1.size(), range2.size());
    const uint64_t action_scratch_float_count =
            max_range_size * (4ull * estimate.max_actions_per_node + 2ull);
    const uint64_t training_input_range_bytes =
            estimate_private_cards_vector_bytes(range1.size()) +
            estimate_private_cards_vector_bytes(range2.size());
    const uint64_t best_response_bytes =
            sizeof(BestResponse) +
            estimate_private_cards_vector_bytes(range1.size()) +
            estimate_private_cards_vector_bytes(range2.size()) +
            reach_prob_bytes;
    estimate.working_bytes = best_response_bytes +
                             training_input_range_bytes +
                             bytes_from_floats(action_scratch_float_count);

    const uint64_t base_peak_bytes =
            estimate.persistent_lower_bound_bytes() + estimate.river_cache_bytes + estimate.working_bytes;
    estimate.safety_margin_bytes = std::max<uint64_t>(64ull * kBytesPerMiB, base_peak_bytes / 10ull);

    return estimate;
}
