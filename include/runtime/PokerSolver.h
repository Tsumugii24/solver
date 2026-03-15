//
// Created by Xuefeng Huang on 2020/2/6.
//

#ifndef TEXASSOLVER_POKERSOLVER_H
#define TEXASSOLVER_POKERSOLVER_H

#include <cstdint>
#include <string>
#include <vector>
#include "compairer/Dic5Compairer.h"
#include "tools/PrivateRangeConverter.h"
#include "solver/CfrSolver.h"
#include "solver/PCfrSolver.h"
#include "runtime/ExportFormat.h"
#include "library.h"
using namespace std;

struct SolverMemoryEstimate {
    bool available = false;
    uint64_t tree_bytes = 0;
    uint64_t solver_state_bytes = 0;
    uint64_t trainable_slot_bytes = 0;
    uint64_t trainable_data_bytes = 0;
    uint64_t river_cache_bytes = 0;
    uint64_t working_bytes = 0;
    uint64_t safety_margin_bytes = 0;
    uint64_t action_node_count = 0;
    uint64_t chance_node_count = 0;
    uint64_t max_actions_per_node = 0;

    [[nodiscard]] uint64_t persistent_lower_bound_bytes() const {
        return tree_bytes + solver_state_bytes + trainable_slot_bytes + trainable_data_bytes;
    }

    [[nodiscard]] uint64_t likely_peak_bytes() const {
        return persistent_lower_bound_bytes() + river_cache_bytes + working_bytes + safety_margin_bytes;
    }
};

class PokerSolver {
public:
    PokerSolver();
    PokerSolver(string ranks,string suits,string compairer_file,int compairer_file_lines,string compairer_file_bin);
    void load_game_tree(string game_tree_file);
    void build_game_tree(
            float oop_commit,
            float ip_commit,
            int current_round,
            int raise_limit,
            float small_blind,
            float big_blind,
            float stack,
            GameTreeBuildingSettings buildingSettings,
            float allin_threshold
    );
    void train(
            string p1_range,
            string p2_range,
            string boards,
            string log_file,
            int iteration_number,
            int print_interval,
            string algorithm,
            int warmup,
            float accuracy,
            bool use_isomorphism,
            int threads,
             bool enable_equity = false,
             bool enable_range = false
             );
    void dump_strategy(string dump_file,int dump_rounds,SolverDumpFormat dump_format = SolverDumpFormat::PARQUET_JSON);
    SolverMemoryEstimate estimate_memory_details(string p1_range, string p2_range, string boards);
    long long estimate_tree_memory(string p1_range, string p2_range, string boards);
    Deck& getDeck() { return deck; }
private:
    shared_ptr<Dic5Compairer> compairer;
    Deck deck;
    shared_ptr<GameTree> game_tree;
    shared_ptr<Solver> solver;
public:
    const shared_ptr<GameTree> &getGameTree() const;
};


#endif //TEXASSOLVER_POKERSOLVER_H
