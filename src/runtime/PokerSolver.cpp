//
// Created by Xuefeng Huang on 2020/2/6.
//

#include "runtime/PokerSolver.h"
#include <iomanip>
#include <chrono>
#include <sstream>
#include <cmath>

// 自定义 JSON 序列化函数，控制浮点数精度
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

void PokerSolver::dump_strategy(string dump_file,int dump_rounds) {
    auto start_time = std::chrono::high_resolution_clock::now();
    
    // dumps 函数内部会显示进度条
    json dump_json = this->solver->dumps(false,dump_rounds);
    
    auto gen_time = std::chrono::high_resolution_clock::now();
    auto gen_duration = std::chrono::duration_cast<std::chrono::milliseconds>(gen_time - start_time).count();
    cout << "Generation time: " << gen_duration / 1000.0 << "s" << endl;
    
    cout << "Writing to file: " << dump_file << "..." << flush;
    ofstream fileWriter;
    fileWriter.open(dump_file);
    // 使用自定义序列化函数，确保浮点数精度为3位小数
    write_json_with_precision(fileWriter, dump_json, 3, 0, false);
    fileWriter.flush();
    fileWriter.close();
    auto write_time = std::chrono::high_resolution_clock::now();
    auto write_duration = std::chrono::duration_cast<std::chrono::milliseconds>(write_time - gen_time).count();
    cout << " done (" << write_duration / 1000.0 << "s)" << endl;
}

const shared_ptr<GameTree> &PokerSolver::getGameTree() const {
    return game_tree;
}

long long PokerSolver::estimate_tree_memory(string p1_range, string p2_range, string boards) {
    if(this->game_tree == nullptr){
        cout << "Please build tree first." << endl;
        return 0;
    }
    
    vector<string> board_str_arr = string_split(boards,',');
    vector<int> initialBoard;
    for(string one_board_str:board_str_arr){
        initialBoard.push_back(Card::strCard2int(one_board_str));
    }
    
    vector<PrivateCards> range1 = PrivateRangeConverter::rangeStr2Cards(p1_range,initialBoard);
    vector<PrivateCards> range2 = PrivateRangeConverter::rangeStr2Cards(p2_range,initialBoard);
    
    int deck_num = this->deck.getCards().size() - initialBoard.size();
    return this->game_tree->estimate_tree_memory(deck_num, range1.size(), range2.size());
}
