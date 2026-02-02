//
// Created by Xuefeng Huang on 2020/1/31.
//

#ifndef TEXASSOLVER_PCFRSOLVER_H
#define TEXASSOLVER_PCFRSOLVER_H
#include <ranges/PrivateCards.h>
#include <compairer/Compairer.h>
#include <Deck.h>
#include <ranges/RiverRangeManager.h>
#include <ranges/PrivateCardsManager.h>
#include <trainable/CfrPlusTrainable.h>
#include <trainable/DiscountedCfrTrainable.h>
#include "Solver.h"
#include <omp.h>
#include "tools/lookup8.h"
#include "tools/utils.h"
#include <queue>
#include <optional>
#include <atomic>

template<typename T>
class ThreadsafeQueue {
    std::queue<T> queue_;
    mutable std::mutex mutex_;

    // Moved out of public interface to prevent races between this
    // and pop().
    bool empty() const {
        return queue_.empty();
    }

public:
    ThreadsafeQueue() = default;
    ThreadsafeQueue(const ThreadsafeQueue<T> &) = delete ;
    ThreadsafeQueue& operator=(const ThreadsafeQueue<T> &) = delete ;

    ThreadsafeQueue(ThreadsafeQueue<T>&& other) {
        std::lock_guard<std::mutex> lock(mutex_);
        queue_ = std::move(other.queue_);
    }

    virtual ~ThreadsafeQueue() { }

    unsigned long size() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return queue_.size();
    }

    std::optional<T> pop() {
        std::lock_guard<std::mutex> lock(mutex_);
        if (queue_.empty()) {
            return {};
        }
        T tmp = queue_.front();
        queue_.pop();
        return tmp;
    }

    void push(const T &item) {
        std::lock_guard<std::mutex> lock(mutex_);
        queue_.push(item);
    }
};

struct TaskParams{
    int player;
    shared_ptr<GameTreeNode> node;
    const vector<float> &reach_probs;
    int iter;
    uint64_t current_board;
    int deal;
};

// CFR 递归返回结构，包含 payoffs 和 equity
struct CfrResult {
    vector<float> payoffs;
    vector<float> equity;  // equity = win_prob + tie_prob/2
    
    CfrResult() = default;
    CfrResult(size_t size) : payoffs(size, 0.0f), equity(size, 0.0f) {}
    CfrResult(vector<float> p, vector<float> e) : payoffs(std::move(p)), equity(std::move(e)) {}
};

class PCfrSolver:public Solver {
public:
    PCfrSolver(shared_ptr<GameTree> tree,
            vector<PrivateCards> range1 ,
            vector<PrivateCards> range2,
            vector<int> initial_board,
            shared_ptr<Compairer> compairer,
            Deck deck,
            int iteration_number,
            bool debug,
            int print_interval,
            string logfile,
            string trainer,
            Solver::MonteCarolAlg monteCarolAlg,
            int warmup,
            float accuracy,
            bool use_isomorphism,
            int num_threads,
            bool enable_equity = false,
            bool enable_range = false
    );
    void train() override;
    json dumps(bool with_status,int depth);
private:
    vector<vector<PrivateCards>> ranges;
    vector<PrivateCards> range1;
    vector<PrivateCards> range2;
    vector<int> initial_board;
    uint64_t initial_board_long;
    shared_ptr<Compairer> compairer;
    int color_iso_offset[52 * 52 * 2][4] = {0};

    Deck deck;
    RiverRangeManager rrm;
    int player_number;
    int iteration_number;
    PrivateCardsManager pcm;
    bool debug;
    int print_interval;
    string trainer;
    string logfile;
    Solver::MonteCarolAlg monteCarolAlg;
    vector<int> round_deal;
    int num_threads;
    int warmup;
    GameTreeNode::GameRound root_round;
    GameTreeNode::GameRound split_round;
    bool distributing_task;
    float accuracy;
    bool use_isomorphism;
    bool enable_equity;
    bool enable_range;

    const vector<PrivateCards>& playerHands(int player);
    vector<vector<float>> getReachProbs();
    static vector<PrivateCards> noDuplicateRange(const vector<PrivateCards>& private_range,uint64_t board_long);
    void setTrainable(shared_ptr<GameTreeNode> root);
    CfrResult cfr(int player, shared_ptr<GameTreeNode> node, const vector<float>& reach_probs, int iter, uint64_t current_board,int deal);
    vector<int> getAllAbstractionDeal(int deal);
    CfrResult chanceUtility(int player,shared_ptr<ChanceNode> node,const vector<float>& reach_probs,int iter,uint64_t current_boardi,int deal);
    CfrResult showdownUtility(int player,shared_ptr<ShowdownNode> node,const vector<float>& reach_probs,int iter,uint64_t current_board,int deal);
    CfrResult actionUtility(int player,shared_ptr<ActionNode> node,const vector<float>& reach_probs,int iter,uint64_t current_board,int deal);
    CfrResult terminalUtility(int player,shared_ptr<TerminalNode> node,const vector<float>& reach_prob,int iter,uint64_t current_board,int deal);
    void findGameSpecificIsomorphisms();
    void purnTree();
    void exchangeRange(json& strategy,int rank1,int rank2,shared_ptr<ActionNode> one_node);
    void exchangeRangeProbs(json& range_data,int rank1,int rank2,shared_ptr<ActionNode> one_node);
    void reConvertJson(const shared_ptr<GameTreeNode>& node,json& strategy,string key,int depth,int max_depth,vector<string> prefix,int deal,vector<vector<int>> exchange_color_list,const vector<vector<float>>& reach_probs);
    
    // 进度条相关
    long long countNodes(const shared_ptr<GameTreeNode>& node, int depth, int max_depth);
    mutable std::atomic<long long> dump_progress{0};
    mutable long long dump_total{0};
    void printProgress(long long current, long long total, const std::string& prefix = "") const;

};


#endif //TEXASSOLVER_PCFRSOLVER_H
