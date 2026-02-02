//
// Created by Xuefeng Huang on 2020/1/31.
//

#include <solver/BestResponse.h>
#include "solver/PCfrSolver.h"

//#define DEBUG;

PCfrSolver::PCfrSolver(shared_ptr<GameTree> tree, vector<PrivateCards> range1, vector<PrivateCards> range2,
                     vector<int> initial_board, shared_ptr<Compairer> compairer, Deck deck, int iteration_number, bool debug,
                     int print_interval, string logfile, string trainer, Solver::MonteCarolAlg monteCarolAlg,int warmup,float accuracy,bool use_isomorphism,int num_threads,bool enable_equity,bool enable_range) :Solver(tree){
    this->initial_board = initial_board;
    this->initial_board_long = Card::boardInts2long(initial_board);
    this->logfile = logfile;
    this->trainer = trainer;
    this->warmup = warmup;

    range1 = this->noDuplicateRange(range1,initial_board_long);
    range2 = this->noDuplicateRange(range2,initial_board_long);

    this->range1 = range1;
    this->range2 = range2;
    this->player_number = 2;
    this->ranges = vector<vector<PrivateCards>>(this->player_number);
    this->ranges[0] = range1;
    this->ranges[1] = range2;

    this->compairer = compairer;

    this->deck = deck;
    this->use_isomorphism = use_isomorphism;
    this->enable_equity = enable_equity;
    this->enable_range = enable_range;

    this->rrm = RiverRangeManager(compairer);
    this->iteration_number = iteration_number;

    vector<vector<PrivateCards>> private_cards(this->player_number);
    private_cards[0] = range1;
    private_cards[1] = range2;
    pcm = PrivateCardsManager(private_cards,this->player_number,Card::boardInts2long(this->initial_board));
    this->debug = debug;
    this->print_interval = print_interval;
    this->monteCarolAlg = monteCarolAlg;
    this->accuracy = accuracy;
    if(num_threads == -1){
        num_threads = omp_get_num_procs();
    }
    cout << fmt::format("Using {} threads",num_threads) << endl;
    this->num_threads = num_threads;
    this->distributing_task = false;
    omp_set_num_threads(this->num_threads);
    setTrainable(this->tree->getRoot());
    this->root_round = this->tree->getRoot()->getRound();
    if(this->root_round == GameTreeNode::GameRound::PREFLOP){
        this->split_round = GameTreeNode::GameRound::FLOP;
    }else if(this->root_round == GameTreeNode::GameRound::FLOP){
        this->split_round = GameTreeNode::GameRound::TURN;
    }else if(this->root_round == GameTreeNode::GameRound::TURN){
        this->split_round = GameTreeNode::GameRound::RIVER;
    }else{
        // do not use multithread in river, really not necessary
        this->split_round = GameTreeNode::GameRound::PREFLOP;
    }
}

const vector<PrivateCards> &PCfrSolver::playerHands(int player) {
    if(player == 0){
        return range1;
    }else if (player == 1){
        return range2;
    }else{
        throw runtime_error("player not found");
    }
}

vector<vector<float>> PCfrSolver::getReachProbs() {
    vector<vector<float>> retval(this->player_number);
    for(int player = 0;player < this->player_number;player ++){
        vector<PrivateCards> player_cards = this->playerHands(player);
        vector<float> reach_prob(player_cards.size());
        for(int i = 0;i < player_cards.size();i ++){
            reach_prob[i] = player_cards[i].weight;
        }
        retval[player] = reach_prob;
    }
    return retval;
}

vector<PrivateCards>
PCfrSolver::noDuplicateRange(const vector<PrivateCards> &private_range, uint64_t board_long) {
    vector<PrivateCards> range_array;
    unordered_map<int,bool> rangekv;
    for(PrivateCards one_range:private_range){
        if(rangekv.find(one_range.hashCode()) != rangekv.end())
            throw runtime_error(fmt::format("duplicated key {}",one_range.toString()));
        rangekv[one_range.hashCode()] = true;
        uint64_t hand_long = Card::boardInts2long(one_range.get_hands());
        if(!Card::boardsHasIntercept(hand_long,board_long)){
            range_array.push_back(one_range);
        }
    }
    return range_array;

}

void PCfrSolver::setTrainable(shared_ptr<GameTreeNode> root) {
    if(root->getType() == GameTreeNode::ACTION){
        shared_ptr<ActionNode> action_node = std::dynamic_pointer_cast<ActionNode>(root);

        int player = action_node->getPlayer();

        if(this->trainer == "cfr_plus"){
            //vector<PrivateCards> player_privates = this->ranges[player];
            //action_node->setTrainable(make_shared<CfrPlusTrainable>(action_node,player_privates));
            throw runtime_error(fmt::format("trainer {} not supported",this->trainer));
        }else if(this->trainer == "discounted_cfr"){
            vector<PrivateCards>* player_privates = &this->ranges[player];
            //action_node->setTrainable(make_shared<DiscountedCfrTrainable>(action_node,player_privates));
            int num;
            GameTreeNode::GameRound gr = this->tree->getRoot()->getRound();
            int root_round = GameTreeNode::gameRound2int(gr);
            int current_round = GameTreeNode::gameRound2int(root->getRound());
            int gap = current_round - root_round;

            if(gap == 2) {
                num = this->deck.getCards().size() * this->deck.getCards().size() +
                          this->deck.getCards().size() + 1;
            }else if(gap == 1) {
                num = this->deck.getCards().size() + 1;
            }else if(gap == 0) {
                num =  1;
            }else{
                throw runtime_error("gap not understand");
            }
            action_node->setTrainable(vector<shared_ptr<Trainable>>(num),player_privates);
        }else{
            throw runtime_error(fmt::format("trainer {} not found",this->trainer));
        }

        vector<shared_ptr<GameTreeNode>> childrens =  action_node->getChildrens();
        for(shared_ptr<GameTreeNode> one_child:childrens) setTrainable(one_child);
    }else if(root->getType() == GameTreeNode::CHANCE) {
        shared_ptr<ChanceNode> chance_node = std::dynamic_pointer_cast<ChanceNode>(root);
        shared_ptr<GameTreeNode> children = chance_node->getChildren();
        setTrainable(children);
    }
    else if(root->getType() == GameTreeNode::TERMINAL){

    }else if(root->getType() == GameTreeNode::SHOWDOWN){

    }
}

vector<int> PCfrSolver::getAllAbstractionDeal(int deal){
    vector<int> all_deal;
    int card_num = this->deck.getCards().size();
    if(deal == 0){
        all_deal.push_back(deal);
    } else if (deal > 0 && deal <= card_num){
        int origin_deal = int((deal - 1) / 4) * 4;
        for(int i = 0;i < 4;i ++){
            int one_card = origin_deal + i + 1;

            Card *first_card = const_cast<Card *>(&(this->deck.getCards()[origin_deal + i]));
            uint64_t first_long = Card::boardInt2long(
                    first_card->getCardInt());
            if (Card::boardsHasIntercept(first_long, this->initial_board_long))continue;
            all_deal.push_back(one_card);
        }
    } else{
        //cout << "______________________" << endl;
        int c_deal = deal - (1 + card_num);
        int first_deal = int((c_deal / card_num) / 4) * 4;
        int second_deal = int((c_deal % card_num) / 4) * 4;

        for(int i = 0;i < 4;i ++) {
            for(int j = 0;j < 4;j ++) {
                if(first_deal == second_deal && i == j) continue;

                Card *first_card = const_cast<Card *>(&(this->deck.getCards()[first_deal + i]));
                uint64_t first_long = Card::boardInt2long(
                        first_card->getCardInt());
                if (Card::boardsHasIntercept(first_long, this->initial_board_long))continue;

                Card *second_card = const_cast<Card *>(&(this->deck.getCards()[second_deal + j]));
                uint64_t second_long = Card::boardInt2long(
                        second_card->getCardInt());
                if (Card::boardsHasIntercept(second_long, this->initial_board_long))continue;

                int one_card = card_num * (first_deal + i) + (second_deal + j) + 1 + card_num;
                //cout << ";" << this->deck.getCards()[first_deal + i].toString() << "," << this->deck.getCards()[second_deal + j].toString();
                all_deal.push_back(one_card);
            }
        }
        //cout << endl;
    }
    return all_deal;
}

CfrResult PCfrSolver::cfr(int player, shared_ptr<GameTreeNode> node, const vector<float> &reach_probs, int iter,
                                    uint64_t current_board,int deal) {
    switch(node->getType()) {
        case GameTreeNode::ACTION: {
            shared_ptr<ActionNode> action_node = std::dynamic_pointer_cast<ActionNode>(node);
            return actionUtility(player, action_node, reach_probs, iter, current_board,deal);
        }case GameTreeNode::SHOWDOWN: {
            shared_ptr<ShowdownNode> showdown_node = std::dynamic_pointer_cast<ShowdownNode>(node);
            return showdownUtility(player, showdown_node, reach_probs, iter, current_board,deal);
        }case GameTreeNode::TERMINAL: {
            shared_ptr<TerminalNode> terminal_node = std::dynamic_pointer_cast<TerminalNode>(node);
            return terminalUtility(player, terminal_node, reach_probs, iter, current_board,deal);
        }case GameTreeNode::CHANCE: {
            shared_ptr<ChanceNode> chance_node = std::dynamic_pointer_cast<ChanceNode>(node);
            return chanceUtility(player, chance_node, reach_probs, iter, current_board,deal);
        }default:
            throw runtime_error("node type unknown");
    }
}

CfrResult
PCfrSolver::chanceUtility(int player, shared_ptr<ChanceNode> node, const vector<float> &reach_probs, int iter,
                         uint64_t current_board,int deal) {
    vector<Card>& cards = this->deck.getCards();
    //float[] cardWeights = getCardsWeights(player,reach_probs[1 - player],current_board);

    int card_num = node->getCards().size();
    if(card_num % 4 != 0) throw runtime_error("card num cannot round 4");
    // 可能的发牌情况,2代表每个人的holecard是两张
    int possible_deals = node->getCards().size() - Card::long2board(current_board).size() - 2;
    int oppo = 1 - player;

    //vector<float> chance_utility(reach_probs[player].size());
    vector<float> chance_utility = vector<float>(this->ranges[player].size());
    vector<float> chance_equity;  // 只在启用 equity 时分配
    fill(chance_utility.begin(),chance_utility.end(),0);
    if(this->enable_equity) {
        chance_equity = vector<float>(this->ranges[player].size());
        fill(chance_equity.begin(),chance_equity.end(),0);
    }

    int random_deal = 0;
    if(this->monteCarolAlg==MonteCarolAlg::PUBLIC) {
        if (this->round_deal[GameTreeNode::gameRound2int(node->getRound())] == -1) {
            random_deal = random(1, possible_deals + 1 + 2);
            this->round_deal[GameTreeNode::gameRound2int(node->getRound())] = random_deal;
        } else {
            random_deal = this->round_deal[GameTreeNode::gameRound2int(node->getRound())];
        }
    }
    //vector<vector<vector<float>>> arr_new_reach_probs = vector<vector<vector<float>>>(node->getCards().size());

    vector<CfrResult> results(node->getCards().size());

    vector<float> multiplier;
    if(iter <= this->warmup){
        multiplier = vector<float>(card_num);
        fill(multiplier.begin(), multiplier.end(), 0);
        for (int card_base = 0; card_base < card_num / 4; card_base++) {
            int cardr = std::rand() % 4;
            int card_target = card_base * 4 + cardr;
            int multiplier_num = 0;
            for (int i = 0; i < 4; i++) {
                int i_card = card_base * 4 + i;
                if (i == cardr) {
                    Card *one_card = const_cast<Card *>(&(node->getCards()[i_card]));
                    uint64_t card_long = Card::boardInt2long(
                            one_card->getCardInt());
                    if (!Card::boardsHasIntercept(card_long, current_board)) {
                        multiplier_num += 1;
                    }
                } else {
                    Card *one_card = const_cast<Card *>(&(node->getCards()[i_card]));
                    uint64_t card_long = Card::boardInt2long(
                            one_card->getCardInt());
                    if (!Card::boardsHasIntercept(card_long, current_board)) {
                        multiplier_num += 1;
                    }
                }
            }
            multiplier[card_target] = multiplier_num;
        }
    }

    vector<int> valid_cards;
    valid_cards.reserve(node->getCards().size());

    for(int card = 0;card < node->getCards().size();card ++) {
        shared_ptr<GameTreeNode> one_child = node->getChildren();
        Card *one_card = const_cast<Card *>(&(node->getCards()[card]));
        uint64_t card_long = Card::boardInt2long(one_card->getCardInt());//Card::boardCards2long(new Card[]{one_card});
        if (Card::boardsHasIntercept(card_long, current_board)) continue;
        if (iter <= this->warmup && multiplier[card] == 0) continue;
        if (this->color_iso_offset[deal][one_card->getCardInt() % 4] < 0) continue;
        valid_cards.push_back(card);
    }

    #pragma omp parallel for schedule(static)
    for(int valid_ind = 0;valid_ind < valid_cards.size();valid_ind++) {
        int card = valid_cards[valid_ind];
        shared_ptr<GameTreeNode> one_child = node->getChildren();
        Card *one_card = const_cast<Card *>(&(node->getCards()[card]));
        uint64_t card_long = Card::boardInt2long(one_card->getCardInt());//Card::boardCards2long(new Card[]{one_card});

        uint64_t new_board_long = current_board | card_long;
        if (this->monteCarolAlg == MonteCarolAlg::PUBLIC) {
            throw runtime_error("parallel solver don't support public monte carol");
        }

        //cout << "Card deal:" << one_card->toString() << endl;

        vector<PrivateCards> &playerPrivateCard = (this->ranges[player]);
        vector<PrivateCards> &oppoPrivateCards = (this->ranges[1 - player]);


        vector<float> new_reach_probs = vector<float>(oppoPrivateCards.size());



#ifdef DEBUG
        if (playerPrivateCard.size() != this->ranges[player].size()) throw runtime_error("length not match");
        if (oppoPrivateCards.size() != this->ranges[1 - player].size()) throw runtime_error("length not match");
#endif

        int player_hand_len = this->ranges[oppo].size();
        for (int player_hand = 0; player_hand < player_hand_len; player_hand++) {
            PrivateCards &one_private = this->ranges[oppo][player_hand];
            uint64_t privateBoardLong = one_private.toBoardLong();
            if (Card::boardsHasIntercept(card_long, privateBoardLong)) {
                new_reach_probs[player_hand] = 0;
                continue;
            }
            new_reach_probs[player_hand] = reach_probs[player_hand] / possible_deals;
        }
#ifdef DEBUG
        if (Card::boardsHasIntercept(current_board, card_long))
            throw runtime_error("board has intercept with dealt card");
#endif

        int new_deal;
        if(deal == 0){
            new_deal = card + 1;
        } else if (deal > 0 && deal <= card_num){
            int origin_deal = deal - 1;

#ifdef DEBUG
            if(origin_deal == card) throw runtime_error("deal should not be equal");
#endif
            new_deal = card_num * origin_deal + card;
            new_deal += (1 + card_num);
        } else{
            throw runtime_error(fmt::format("deal out of range : {} ",deal));
        }
        if(this->distributing_task && node->getRound() == this->split_round) {
            results[one_card->getNumberInDeckInt()] = CfrResult(this->ranges[player].size());
            //TaskParams taskParams = TaskParams();
        }else {
            CfrResult child_result = this->cfr(player, one_child, new_reach_probs, iter, new_board_long, new_deal);
            results[one_card->getNumberInDeckInt()] = std::move(child_result);
        }
    }

    // 方案 B：equity 和 payoffs 使用完全相同的累加逻辑（直接累加）
    for(int card = 0;card < node->getCards().size();card ++) {
        Card *one_card = const_cast<Card *>(&(node->getCards()[card]));
        CfrResult child_result;
        int offset = this->color_iso_offset[deal][one_card->getCardInt() % 4];
        if(offset < 0) {
            int rank1 = one_card->getCardInt() % 4;
            int rank2 = rank1 + offset;
#ifdef DEBUG
            if(rank2 < 0) throw runtime_error("rank error");
#endif
            child_result = results[one_card->getNumberInDeckInt() + offset];
            exchange_color(child_result.payoffs,this->pcm.getPreflopCards(player),rank1,rank2);
            if(this->enable_equity && !child_result.equity.empty()) {
                exchange_color(child_result.equity,this->pcm.getPreflopCards(player),rank1,rank2);
            }
        }else{
            child_result = results[one_card->getNumberInDeckInt()];
        }
        if(child_result.payoffs.empty())
            continue;

#ifdef DEBUG
        if(child_result.payoffs.size() != chance_utility.size()) throw runtime_error("length not match");
#endif
        
        if(iter > this->warmup) {
            for (int i = 0; i < child_result.payoffs.size(); i++) {
                chance_utility[i] += child_result.payoffs[i];
            }
            if(this->enable_equity && !child_result.equity.empty()) {
                for (int i = 0; i < child_result.equity.size(); i++) {
                    chance_equity[i] += child_result.equity[i];
                }
            }
        }else{
            for (int i = 0; i < child_result.payoffs.size(); i++) {
                chance_utility[i] += child_result.payoffs[i] * multiplier[card];
            }
            if(this->enable_equity && !child_result.equity.empty()) {
                for (int i = 0; i < child_result.equity.size(); i++) {
                    chance_equity[i] += child_result.equity[i] * multiplier[card];
                }
            }
        }
    }

#ifdef DEBUG
    if(this->monteCarolAlg == MonteCarolAlg::PUBLIC) {
        throw runtime_error("not possible");
    }
    if(chance_utility.size() != this->ranges[player].size()) {
        throw runtime_error("size problems");
    }
#endif
    return CfrResult(std::move(chance_utility), std::move(chance_equity));
}

CfrResult
PCfrSolver::actionUtility(int player, shared_ptr<ActionNode> node, const vector<float> &reach_probs, int iter,
                         uint64_t current_board,int deal) {
    int oppo = 1 - player;
    const vector<PrivateCards>& node_player_private_cards = this->ranges[node->getPlayer()];

    vector<float> payoffs = vector<float>(this->ranges[player].size());
    vector<float> total_equity;  // 只在启用 equity 时分配
    fill(payoffs.begin(),payoffs.end(),0);
    if(this->enable_equity) {
        total_equity = vector<float>(this->ranges[player].size());
        fill(total_equity.begin(),total_equity.end(),0);
    }
    vector<shared_ptr<GameTreeNode>>& children =  node->getChildrens();
    vector<GameActions>& actions =  node->getActions();

    shared_ptr<Trainable> trainable;

    /*
    if(iter <= this->warmup){
        vector<int> deals = this->getAllAbstractionDeal(deal);
        trainable = node->getTrainable(deals[0]);
    }else{
        trainable = node->getTrainable(deal);
    }
     */
    trainable = node->getTrainable(deal);

#ifdef DEBUG
    if(trainable == nullptr){
        throw runtime_error("null trainable");
    }
#endif

    const vector<float> current_strategy = trainable->getcurrentStrategy();
#ifdef DEBUG
    if (current_strategy.size() != actions.size() * node_player_private_cards.size()) {
        node->printHistory();
        throw runtime_error(fmt::format(
                "length not match {} - {} \n action size {} private_card size {}"
                ,current_strategy.size()
                ,actions.size() * node_player_private_cards.size()
                ,actions.size()
                ,node_player_private_cards.size()
        ));
    }
#endif

    //为了节省计算成本将action regret 存在一位数组而不是二维数组中，两个纬度分别是（该infoset有多少动作,该palyer有多少holecard）
    vector<float> regrets(actions.size() * node_player_private_cards.size());

    vector<vector<float>> all_action_utility(actions.size());
    vector<vector<float>> all_action_equity;  // 只在启用 equity 时分配
    if(this->enable_equity) {
        all_action_equity = vector<vector<float>>(actions.size());
    }
    int node_player = node->getPlayer();

    vector<CfrResult> results(actions.size());
    for (int action_id = 0; action_id < actions.size(); action_id++) {

        if (node_player != player) {
            vector<float> new_reach_prob = vector<float>(reach_probs.size());
            for (int hand_id = 0; hand_id < new_reach_prob.size(); hand_id++) {
                float strategy_prob = current_strategy[hand_id + action_id * node_player_private_cards.size()];
                new_reach_prob[hand_id] = reach_probs[hand_id] * strategy_prob;
            }
            //#pragma omp task shared(results,action_id)
            results[action_id] = this->cfr(player, children[action_id], new_reach_prob, iter,
                                           current_board,deal);
        }else {
            //#pragma omp task shared(results,action_id)
            results[action_id] = this->cfr(player, children[action_id], reach_probs, iter,
                                           current_board,deal);
        }

    }

    //#pragma omp taskwait
    
    for (int action_id = 0; action_id < actions.size(); action_id++) {
        vector<float>& action_utilities = results[action_id].payoffs;
        if(action_utilities.empty()){
            continue;
        }
        all_action_utility[action_id] = action_utilities;
        if(this->enable_equity && !results[action_id].equity.empty()) {
            all_action_equity[action_id] = results[action_id].equity;
        }

        // cfr结果是每手牌的收益，payoffs代表的也是每手牌的收益，他们的长度理应相等
#ifdef DEBUG
        if (action_utilities.size() != payoffs.size()) {
            cout << ("errmsg") << endl;
            cout << (fmt::format("node player {} ", node->getPlayer())) << endl;
            node->printHistory();
            throw runtime_error(
                    fmt::format(
                            "action and payoff length not match {} - {}", action_utilities.size(),
                            payoffs.size()
                    )
            );
        }
#endif

        // 方案 B：equity 和 payoffs 使用完全相同的累加逻辑
        for (int hand_id = 0; hand_id < action_utilities.size(); hand_id++) {
            if (player == node->getPlayer()) {
                float strategy_prob = current_strategy[hand_id + action_id * node_player_private_cards.size()];
                payoffs[hand_id] += strategy_prob * action_utilities[hand_id];
                if(this->enable_equity && !results[action_id].equity.empty()) {
                    total_equity[hand_id] += strategy_prob * results[action_id].equity[hand_id];
                }
            } else {
                payoffs[hand_id] += action_utilities[hand_id];
                // 方案 B：直接累加，和 payoffs 完全一致
                if(this->enable_equity && !results[action_id].equity.empty()) {
                    total_equity[hand_id] += results[action_id].equity[hand_id];
                }
            }
        }
    }


    if (player == node->getPlayer()) {
        for (int i = 0; i < node_player_private_cards.size(); i++) {
            //boolean regrets_all_negative = true;
            for (int action_id = 0; action_id < actions.size(); action_id++) {
                // 下面是regret计算的伪代码
                // regret[action_id * player_hc: (action_id + 1) * player_hc]
                //     = all_action_utilitiy[action_id] - payoff[action_id]
                regrets[action_id * node_player_private_cards.size() + i] =
                        (all_action_utility[action_id])[i] - payoffs[i];
            }
        }

        if(!this->distributing_task) {
            if (iter > this->warmup) {
                trainable->updateRegrets(regrets, iter + 1, reach_probs);
            }/*else if(iter < this->warmup){
            vector<int> deals = this->getAllAbstractionDeal(deal);
            shared_ptr<Trainable> one_trainable = node->getTrainable(deals[0]);
            one_trainable->updateRegrets(regrets, iter + 1, reach_probs[player]);
            }*/
            else {
                // iter == this->warmup
                vector<int> deals = this->getAllAbstractionDeal(deal);
                shared_ptr<Trainable> standard_trainable = nullptr;
                for (int one_deal : deals) {
                    shared_ptr<Trainable> one_trainable = node->getTrainable(one_deal);
                    if (standard_trainable == nullptr) {
                        one_trainable->updateRegrets(regrets, iter + 1, reach_probs);
                        standard_trainable = one_trainable;
                    } else {
                        one_trainable->copyStrategy(standard_trainable);
                    }
                }
            }
        }

        // 计算并存储 EV (每隔 print_interval 次迭代计算一次)
        if(iter % this->print_interval == 0){
            float oppo_sum = 0;
            vector<float> oppo_card_sum = vector<float> (52);
            fill(oppo_card_sum.begin(),oppo_card_sum.end(),0);

            const vector<PrivateCards>& oppo_hand = playerHands(oppo);
            for(std::size_t i = 0;i < oppo_hand.size();i ++){
                oppo_card_sum[oppo_hand[i].card1] += reach_probs[i];
                oppo_card_sum[oppo_hand[i].card2] += reach_probs[i];
                oppo_sum += reach_probs[i];
            }

            const vector<PrivateCards>& player_hand = playerHands(player);
            vector<float> evs(actions.size() * node_player_private_cards.size(), 0.0);

            for (std::size_t action_id = 0; action_id < actions.size(); action_id++) {
                for (std::size_t hand_id = 0; hand_id < node_player_private_cards.size(); hand_id++) {
                    float one_ev = (all_action_utility)[action_id][hand_id];

                    int oppo_same_card_ind = this->pcm.indPlayer2Player(player, oppo, hand_id);
                    float plus_reach_prob;

                    const PrivateCards& one_player_hand = player_hand[hand_id];
                    if(oppo_same_card_ind == -1){
                        plus_reach_prob = 0;
                    }else{
                        plus_reach_prob = reach_probs[oppo_same_card_ind];
                    }

                    float rp_sum = (
                        oppo_sum - oppo_card_sum[one_player_hand.card1]
                        - oppo_card_sum[one_player_hand.card2]
                        + plus_reach_prob);

                    std::size_t idx = hand_id + action_id * node_player_private_cards.size();
                    // 归一化 EV
                    evs[idx] = (rp_sum > 0) ? one_ev / rp_sum : 0;
                }
            }
            trainable->setEv(evs);
            
            // 方案 B：用 rp_sum 归一化 equity（和 EV 完全一致）
            if(this->enable_equity && !all_action_equity.empty()) {
                vector<float> equity_to_store(actions.size() * node_player_private_cards.size(), 0.0);
                for (std::size_t action_id = 0; action_id < actions.size(); action_id++) {
                    if(all_action_equity[action_id].empty()) continue;
                    for (std::size_t hand_id = 0; hand_id < node_player_private_cards.size(); hand_id++) {
                        float one_equity = (all_action_equity)[action_id][hand_id];
                        
                        // 计算 rp_sum（和 EV 一样）
                        int oppo_same_card_ind = this->pcm.indPlayer2Player(player, oppo, hand_id);
                        float plus_reach_prob;
                        const PrivateCards& one_player_hand = player_hand[hand_id];
                        if(oppo_same_card_ind == -1){
                            plus_reach_prob = 0;
                        }else{
                            plus_reach_prob = reach_probs[oppo_same_card_ind];
                        }
                        float rp_sum = (
                            oppo_sum - oppo_card_sum[one_player_hand.card1]
                            - oppo_card_sum[one_player_hand.card2]
                            + plus_reach_prob);
                        
                        std::size_t idx = hand_id + action_id * node_player_private_cards.size();
                        // 归一化 equity（和 EV 一样）
                        equity_to_store[idx] = (rp_sum > 0) ? one_equity / rp_sum : 0;
                    }
                }
                trainable->setEquity(equity_to_store);
            }
        }
    }
    return CfrResult(std::move(payoffs), std::move(total_equity));

}

CfrResult
PCfrSolver::showdownUtility(int player, shared_ptr<ShowdownNode> node, const vector<float> &reach_probs,
                           int iter, uint64_t current_board,int deal) {
    // player win时候player的收益，player lose的时候收益明显为-player_payoff
    int oppo = 1 - player;
    float win_payoff = node->get_payoffs(ShowdownNode::ShowDownResult::NOTTIE,player,player);
    float lose_payoff = node->get_payoffs(ShowdownNode::ShowDownResult::NOTTIE,oppo,player);
    const vector<PrivateCards>& player_private_cards = this->ranges[player];
    const vector<PrivateCards>& oppo_private_cards = this->ranges[oppo];

    const vector<RiverCombs>& player_combs = this->rrm.getRiverCombos(player,player_private_cards,current_board);
    const vector<RiverCombs>& oppo_combs = this->rrm.getRiverCombos(oppo,oppo_private_cards,current_board);

    vector<float> payoffs = vector<float>(player_private_cards.size());
    vector<float> equity;  // 只在启用 equity 时分配
    
    // 只在启用 equity 时分配临时存储（方案 B：和 EV 一致，不标准化）
    vector<float> effective_winsum_arr;
    vector<float> effective_total_arr;
    float oppo_total = 0;
    vector<float> oppo_card_total;
    
    if(this->enable_equity) {
        equity = vector<float>(player_private_cards.size(), 0.0f);
        effective_winsum_arr = vector<float>(player_private_cards.size(), 0.0f);
        effective_total_arr = vector<float>(player_private_cards.size(), 0.0f);
        oppo_card_total = vector<float>(52, 0.0f);
        
        // 计算对手总 reach_prob 和每张牌的分布（用于计算 effective_total）
        for(std::size_t i = 0;i < oppo_combs.size();i ++){
            const RiverCombs& one_oppo_comb = oppo_combs[i];
            oppo_total += reach_probs[one_oppo_comb.reach_prob_index];
            oppo_card_total[one_oppo_comb.private_cards.card1] += reach_probs[one_oppo_comb.reach_prob_index];
            oppo_card_total[one_oppo_comb.private_cards.card2] += reach_probs[one_oppo_comb.reach_prob_index];
        }
    }

    float winsum = 0;
    vector<float> card_winsum = vector<float> (52);//node->card_sum;
    fill(card_winsum.begin(),card_winsum.end(),0);

    int j = 0;
    for(int i = 0;i < player_combs.size();i ++){
        const RiverCombs& one_player_comb = player_combs[i];
        while (j < oppo_combs.size() && one_player_comb.rank < oppo_combs[j].rank){
            const RiverCombs& one_oppo_comb = oppo_combs[j];
            winsum += reach_probs[one_oppo_comb.reach_prob_index];
            card_winsum[one_oppo_comb.private_cards.card1] += reach_probs[one_oppo_comb.reach_prob_index];
            card_winsum[one_oppo_comb.private_cards.card2] += reach_probs[one_oppo_comb.reach_prob_index];
            j ++;
        }
        float effective_winsum = winsum
                                 - card_winsum[one_player_comb.private_cards.card1]
                                 - card_winsum[one_player_comb.private_cards.card2];
        payoffs[one_player_comb.reach_prob_index] = effective_winsum * win_payoff;
        
        // 只在启用 equity 时存储（方案 B）
        if(this->enable_equity) {
            effective_winsum_arr[one_player_comb.reach_prob_index] = effective_winsum;
            
            // 计算并存储 effective_total（用于后面计算 tiesum）
            float effective_total = oppo_total
                                    - oppo_card_total[one_player_comb.private_cards.card1]
                                    - oppo_card_total[one_player_comb.private_cards.card2];
            // 处理 blocker 重叠
            int oppo_same_card_ind = this->pcm.indPlayer2Player(player, oppo, one_player_comb.reach_prob_index);
            if(oppo_same_card_ind != -1){
                effective_total += reach_probs[oppo_same_card_ind];
            }
            effective_total_arr[one_player_comb.reach_prob_index] = effective_total;
        }
    }

    // 计算失败时的payoff
    float losssum = 0;
    vector<float>& card_losssum = card_winsum;
    fill(card_losssum.begin(),card_losssum.end(),0);

    j = oppo_combs.size() - 1;
    for(int i = player_combs.size() - 1;i >= 0;i --){
        const RiverCombs& one_player_comb = player_combs[i];
        while (j >= 0 && one_player_comb.rank > oppo_combs[j].rank){
            const RiverCombs& one_oppo_comb = oppo_combs[j];
            losssum += reach_probs[one_oppo_comb.reach_prob_index];
            card_losssum[one_oppo_comb.private_cards.card1] += reach_probs[one_oppo_comb.reach_prob_index];
            card_losssum[one_oppo_comb.private_cards.card2] += reach_probs[one_oppo_comb.reach_prob_index];
            j --;
        }
        float effective_losssum = losssum
                                  - card_losssum[one_player_comb.private_cards.card1]
                                  - card_losssum[one_player_comb.private_cards.card2];
        payoffs[one_player_comb.reach_prob_index] += effective_losssum * lose_payoff;
        
        // 方案 B：计算 counterfactual equity（不标准化，和 EV 一致）
        // equity = effective_winsum + 0.5 * effective_tiesum
        if(this->enable_equity) {
            int idx = one_player_comb.reach_prob_index;
            float effective_winsum = effective_winsum_arr[idx];
            float effective_total = effective_total_arr[idx];
            float effective_tiesum = effective_total - effective_winsum - effective_losssum;
            if(effective_tiesum < 0) effective_tiesum = 0;  // 防止浮点误差
            equity[idx] = effective_winsum + 0.5f * effective_tiesum;
        }
    }
    return CfrResult(std::move(payoffs), std::move(equity));
}

CfrResult
PCfrSolver::terminalUtility(int player, shared_ptr<TerminalNode> node, const vector<float> &reach_prob, int iter,
                           uint64_t current_board,int deal) {
    float player_payoff = node->get_payoffs()[player];

    int oppo = 1 - player;
    const vector<PrivateCards>& player_hand = playerHands(player);
    const vector<PrivateCards>& oppo_hand = playerHands(oppo);

    vector<float> payoffs = vector<float>(this->playerHands(player).size());
    vector<float> equity;  // 只在启用 equity 时分配
    
    if(this->enable_equity) {
        // 方案 B：counterfactual equity（和 EV 一致）
        equity = vector<float>(this->playerHands(player).size(), 0.0f);
    }

    float oppo_sum = 0;
    vector<float> oppo_card_sum = vector<float> (52);
    fill(oppo_card_sum.begin(),oppo_card_sum.end(),0);

    for(int i = 0;i < oppo_hand.size();i ++){
        oppo_card_sum[oppo_hand[i].card1] += reach_prob[i];
        oppo_card_sum[oppo_hand[i].card2] += reach_prob[i];
        oppo_sum += reach_prob[i];
    }

    for(int i = 0;i < player_hand.size();i ++){
        const PrivateCards& one_player_hand = player_hand[i];
        if(Card::boardsHasIntercept(current_board,Card::boardInts2long(one_player_hand.get_hands()))){
            continue;
        }
        int oppo_same_card_ind = this->pcm.indPlayer2Player(player,oppo,i);
        float plus_reach_prob;
        if(oppo_same_card_ind == -1){
            plus_reach_prob = 0;
        }else{
            plus_reach_prob = reach_prob[oppo_same_card_ind];
        }
        float effective_oppo_reach = oppo_sum - oppo_card_sum[one_player_hand.card1]
                                     - oppo_card_sum[one_player_hand.card2]
                                     + plus_reach_prob;
        payoffs[i] = player_payoff * effective_oppo_reach;
        
        // 方案 B：counterfactual equity
        // 如果 player 赢（对手 fold）：equity = effective_oppo_reach
        // 如果 player 输（player fold）：equity = 0
        if(this->enable_equity) {
            equity[i] = (player_payoff > 0) ? effective_oppo_reach : 0.0f;
        }
    }

    return CfrResult(std::move(payoffs), std::move(equity));
}

void PCfrSolver::findGameSpecificIsomorphisms() {
    // hand isomorphisms
    vector<Card> board_cards = Card::long2boardCards(this->initial_board_long);
    for(int i = 0;i <= 1;i ++){
        vector<PrivateCards>& range = i == 0?this->range1:this->range2;
        for(int i_range = 0;i_range < range.size();i_range ++) {
            PrivateCards one_range = range[i_range];
            uint32_t range_hash[4]; // four colors, hash of the isomorphisms range + hand combos
            for(int i = 0;i < 4;i ++)range_hash[i] = 0;
            for (int color = 0; color < 4; color++) {
                for (Card one_card:board_cards) {
                    if (one_card.getCardInt() % 4 == color) {
                        range_hash[color] = range_hash[color] | (1 << (one_card.getCardInt() / 4));
                    }
                }
            }
            for (int color = 0; color < 4; color++) {
                for (int one_card_int:{one_range.card1,one_range.card2}) {
                    if (one_card_int % 4 == color) {
                        range_hash[color] = range_hash[color] | (1 << (one_card_int / 4 + 16));
                    }
                }
            }
            // TODO check whethe hash is equal with others
        }
    }

    // chance node isomorphisms
    uint16_t color_hash[4];
    for(int i = 0;i < 4;i ++)color_hash[i] = 0;
    for (Card one_card:board_cards) {
        int rankind = one_card.getCardInt() % 4;
        int suitind = one_card.getCardInt() / 4;
        color_hash[rankind] = color_hash[rankind] | (1 << suitind);
    }
    for(int i = 0;i < 4;i ++){
        this->color_iso_offset[0][i] = 0;
        for(int j = 0;j < i;j ++){
            if(color_hash[i] == color_hash[j]){
                this->color_iso_offset[0][i] = j - i;
                continue;
            }
        }
    }
    for(int deal = 0;deal < this->deck.getCards().size();deal ++) {
        uint16_t color_hash[4];
        for(int i = 0;i < 4;i ++)color_hash[i] = 0;
        // chance node isomorphisms
        for (Card one_card:board_cards) {
            int rankind = one_card.getCardInt() % 4;
            int suitind = one_card.getCardInt() / 4;
            color_hash[rankind] = color_hash[rankind] | (1 << suitind);
        }
        Card one_card = this->deck.getCards()[deal];
        int rankind = one_card.getCardInt() % 4;
        int suitind = one_card.getCardInt() / 4;
        color_hash[rankind] = color_hash[rankind] | (1 << suitind);
        for (int i = 0; i < 4; i++) {
            this->color_iso_offset[deal + 1][i] = 0;
            for (int j = 0; j < i; j++) {
                if (color_hash[i] == color_hash[j]) {
                    this->color_iso_offset[deal + 1][i] = j - i;
                    continue;
                }
            }
        }
    }
}

void PCfrSolver::purnTree() {
    // TODO how to purn the tree, use wramup to start training in memory-save mode, and switch to purn tree directly to both save memory and speedup
}

void PCfrSolver::train() {

    vector<vector<PrivateCards>> player_privates(this->player_number);
    player_privates[0] = pcm.getPreflopCards(0);
    player_privates[1] = pcm.getPreflopCards(1);
    if(this->use_isomorphism){
        this->findGameSpecificIsomorphisms();
    }

    BestResponse br = BestResponse(player_privates,this->player_number,this->pcm,this->rrm,this->deck,this->debug,this->color_iso_offset,this->split_round,this->num_threads);

    br.printExploitability(tree->getRoot(), 0, tree->getRoot()->getPot(), initial_board_long);

    vector<vector<float>> reach_probs = this->getReachProbs();
    ofstream fileWriter;
    if(!this->logfile.empty())fileWriter.open(this->logfile);

    uint64_t begintime = timeSinceEpochMillisec();
    uint64_t endtime = timeSinceEpochMillisec();

    for(int i = 0;i < this->iteration_number;i++){
        for(int player_id = 0;player_id < this->player_number;player_id ++) {
            this->round_deal = vector<int>{-1,-1,-1,-1};
            //#pragma omp parallel
            {
                //#pragma omp single
                {
                    //this->distributing_task = true;
                    cfr(player_id, this->tree->getRoot(), reach_probs[1 - player_id], i, this->initial_board_long,0);
                    //throw runtime_error("returning...");
                }
            }
        }
        if(i % this->print_interval == 0 && i != 0 && i >= this->warmup) {
            endtime = timeSinceEpochMillisec();
            long time_ms = endtime - begintime;
            cout << ("-------------------") << endl;
            float expliotibility = br.printExploitability(tree->getRoot(), i + 1, tree->getRoot()->getPot(), initial_board_long);
            cout << "time used: " << float(time_ms) / 1000 << endl;
            if(!this->logfile.empty()){
                json jo;
                jo["iteration"] = i;
                jo["exploitibility"] = expliotibility;
                jo["time_ms"] = time_ms;
                fileWriter << jo << endl;
            }
            if(expliotibility <= this->accuracy){
                break;
            }
            //begintime = timeSinceEpochMillisec();
        }
    }
    if(!this->logfile.empty()) {
        fileWriter.flush();
        fileWriter.close();
    }
    // Equity 已在 CFR 训练过程中计算完成
}

void PCfrSolver::exchangeRange(json& strategy,int rank1,int rank2,shared_ptr<ActionNode> one_node){
    if(rank1 == rank2)return;
    int player = one_node->getPlayer();
    vector<string> range_strs;
    vector<vector<float>> strategies;

    for(int i = 0;i < this->ranges[player].size();i ++){
        string one_range_str = this->ranges[player][i].toString();
        if(!strategy.contains(one_range_str)){
            for(auto one_key:strategy.items()){
                cout << one_key.key() << endl;
            }
            cout << "strategy: " << strategy  << endl;
            throw runtime_error(fmt::format("{} not exist in strategy",one_range_str));
        }
        vector<float> one_strategy = strategy[one_range_str];
        range_strs.push_back(one_range_str);
        strategies.push_back(one_strategy);
    }
    exchange_color(strategies,this->ranges[player],rank1,rank2);

    for(int i = 0;i < this->ranges[player].size();i ++) {
        string one_range_str = this->ranges[player][i].toString();
        vector<float> one_strategy = strategies[i];
        strategy[one_range_str] = one_strategy;
    }
}

void PCfrSolver::exchangeRangeProbs(json& range_data,int rank1,int rank2,shared_ptr<ActionNode> one_node){
    // 专门用于交换 ranges 数据的函数（每个手牌对应单一 float 值）
    if(rank1 == rank2)return;
    int player = one_node->getPlayer();
    vector<string> range_strs;
    vector<float> probs;

    for(std::size_t i = 0;i < this->ranges[player].size();i ++){
        string one_range_str = this->ranges[player][i].toString();
        if(!range_data.contains(one_range_str)){
            // 对于 ranges，不存在的手牌概率为 0，跳过
            probs.push_back(0.0f);
        } else {
            probs.push_back(range_data[one_range_str].get<float>());
        }
        range_strs.push_back(one_range_str);
    }
    
    // 交换颜色（使用单元素向量包装以复用 exchange_color）
    vector<vector<float>> probs_wrapped;
    for(float p : probs) {
        probs_wrapped.push_back(vector<float>{p});
    }
    exchange_color(probs_wrapped, this->ranges[player], rank1, rank2);

    // 重建 range_data
    range_data.clear();
    for(std::size_t i = 0;i < this->ranges[player].size();i ++) {
        string one_range_str = this->ranges[player][i].toString();
        float prob = probs_wrapped[i][0];
        // 只保存非零的 range
        if(prob > 0) {
            range_data[one_range_str] = prob;
        }
    }
}

void PCfrSolver::reConvertJson(const shared_ptr<GameTreeNode>& node,json& strategy,string key,int depth,int max_depth,vector<string> prefix,int deal,vector<vector<int>> exchange_color_list,const vector<vector<float>>& reach_probs) {
    if(depth >= max_depth) return;
    if(node->getType() == GameTreeNode::GameTreeNodeType::ACTION) {
        json* retval;
        if(key != ""){
            strategy[key] = json();
            retval = &(strategy[key]);
        }else{
            retval = &strategy;
        }

        shared_ptr<ActionNode> one_node = std::dynamic_pointer_cast<ActionNode>(node);
        int node_player = one_node->getPlayer();
        const vector<PrivateCards>& node_player_private_cards = this->ranges[node_player];

        vector<string> actions_str;
        for(GameActions one_action:one_node->getActions()) actions_str.push_back(one_action.toString());

        (*retval)["actions"] = actions_str;
        (*retval)["player"] = node_player;

        // 获取当前策略
        shared_ptr<Trainable> trainable = one_node->getTrainable(deal,false);
        vector<float> current_strategy;
        if(trainable != nullptr) {
            current_strategy = trainable->getAverageStrategy();
        }

        (*retval)["childrens"] = json();
        json& childrens = (*retval)["childrens"];

        for(int i = 0;i < one_node->getActions().size();i ++){
            GameActions& one_action = one_node->getActions()[i];
            shared_ptr<GameTreeNode> one_child = one_node->getChildrens()[i];
            vector<string> new_prefix(prefix);
            new_prefix.push_back(one_action.toString());
            
            // 计算子节点的新 reach_probs
            vector<vector<float>> new_reach_probs = reach_probs;
            if(!current_strategy.empty() && reach_probs[node_player].size() > 0) {
                // 当前玩家的 reach_probs 需要乘以策略概率
                std::size_t hand_count = std::min(reach_probs[node_player].size(), node_player_private_cards.size());
                for(std::size_t hand_id = 0; hand_id < hand_count; hand_id++) {
                    float strategy_prob = current_strategy[hand_id + i * node_player_private_cards.size()];
                    new_reach_probs[node_player][hand_id] = reach_probs[node_player][hand_id] * strategy_prob;
                }
            }
            
            this->reConvertJson(one_child,childrens,one_action.toString(),depth,max_depth,new_prefix,deal,exchange_color_list,new_reach_probs);
        }
        if((*retval)["childrens"].empty()){
            (*retval).erase("childrens");
        }
        
        if(trainable != nullptr) {
            (*retval)["strategy"] = trainable->dump_strategy(false);
            // 导出EV值 (按照GitHub issue的建议)
            (*retval)["evs"] = trainable->dump_evs();
            // 只在启用 equity 时导出
            if(this->enable_equity) {
                (*retval)["equities"] = trainable->dump_equities();
            }
            // 只在启用 range 时导出（导出双方的 range）
            if(this->enable_range) {
                json range_json;
                range_json["player"] = node_player;
                
                // 导出双方的 range（IP = player 0, OOP = player 1）
                for(int p = 0; p < 2; p++) {
                    if(reach_probs[p].size() > 0) {
                        json range_data;
                        vector<PrivateCards>& player_cards = this->ranges[p];
                        std::size_t hand_count = std::min(reach_probs[p].size(), player_cards.size());
                        for(std::size_t hand_id = 0; hand_id < hand_count; hand_id++) {
                            float rp = reach_probs[p][hand_id];
                            // 只输出非零的 range（四舍五入到3位小数后仍大于0的）
                            float rounded_rp = round(rp * 1000.0f) / 1000.0f;
                            if(rounded_rp > 0) {
                                range_data[player_cards[hand_id].toString()] = rounded_rp;
                            }
                        }
                        // IP = player 0, OOP = player 1
                        if(p == 0) {
                            range_json["ip_range"] = std::move(range_data);
                        } else {
                            range_json["oop_range"] = std::move(range_data);
                        }
                    }
                }
                
                (*retval)["ranges"] = std::move(range_json);
            }
            
            for(vector<int> one_exchange:exchange_color_list){
                int rank1 = one_exchange[0];
                int rank2 = one_exchange[1];
                this->exchangeRange((*retval)["strategy"]["strategy"],rank1,rank2,one_node);
                // 同时交换EV值
                if((*retval)["evs"].contains("evs")) {
                    this->exchangeRange((*retval)["evs"]["evs"],rank1,rank2,one_node);
                }
                // 同时交换Equity值（只在启用 equity 时）
                if(this->enable_equity && (*retval)["equities"].contains("equities")) {
                    this->exchangeRange((*retval)["equities"]["equities"],rank1,rank2,one_node);
                }
                // 同时交换Range值（只在启用 range 时）
                if(this->enable_range) {
                    if((*retval)["ranges"].contains("ip_range")) {
                        this->exchangeRangeProbs((*retval)["ranges"]["ip_range"],rank1,rank2,one_node);
                    }
                    if((*retval)["ranges"].contains("oop_range")) {
                        this->exchangeRangeProbs((*retval)["ranges"]["oop_range"],rank1,rank2,one_node);
                    }
                }
            }
        }
        (*retval)["node_type"] = "action_node";
        
        // 更新进度条
        long long current = ++this->dump_progress;
        if(current % 100 == 0 || current == this->dump_total) {
            printProgress(current, this->dump_total, "Generating: ");
        }

    }else if(node->getType() == GameTreeNode::GameTreeNodeType::SHOWDOWN) {
    }else if(node->getType() == GameTreeNode::GameTreeNodeType::TERMINAL) {
    }else if(node->getType() == GameTreeNode::GameTreeNodeType::CHANCE) {
        json* retval;
        if(key != ""){
            strategy[key] = json();
            retval = &(strategy[key]);
        }else{
            retval = &strategy;
        }

        shared_ptr<ChanceNode> chanceNode = std::dynamic_pointer_cast<ChanceNode>(node);
        const vector<Card>& cards = chanceNode->getCards();
        shared_ptr<GameTreeNode> childerns = chanceNode->getChildren();
        vector<string> card_strs;
        for(Card card:cards)
            card_strs.push_back(card.toString());

        json& dealcards = (*retval)["dealcards"];
        for(int i = 0;i < cards.size();i ++){
            vector<vector<int>> new_exchange_color_list(exchange_color_list);
            Card& one_card = const_cast<Card &>(cards[i]);
            vector<string> new_prefix(prefix);
            new_prefix.push_back("Chance:" + one_card.toString());

            int card = i;

            int offset = this->color_iso_offset[deal][one_card.getCardInt() % 4];
            if(offset < 0) {
                for(int x = 0;x < cards.size();x ++){
                    if(
                            Card::card2int(cards[x]) ==
                            (Card::card2int(cards[card]) + offset)
                    ){
                        card = x;
                        break;
                    }
                }
                if(card == i){
                    throw runtime_error("isomorphism not found while dump strategy");
                }
                vector<int> one_exchange{one_card.getCardInt() % 4,one_card.getCardInt() % 4 + offset};
                new_exchange_color_list.push_back(one_exchange);
            }

            int card_num = this->deck.getCards().size();
            int new_deal;
            if(deal == 0){
                new_deal = card + 1;
            } else if (deal > 0 && deal <= card_num){
                int origin_deal = deal - 1;

#ifdef DEBUG
                if(origin_deal == card) throw runtime_error("deal should not be equal");
#endif
                new_deal = card_num * origin_deal + card;
                new_deal += (1 + card_num);
            } else{
                throw runtime_error(fmt::format("deal out of range : {} ",deal));
            }

            if(exchange_color_list.size() > 1){
                throw runtime_error("exchange color list shouldn't be exceed size 1 here");
            }

            string one_card_str = one_card.toString();
            if(exchange_color_list.size() == 1) {
                int rank1 = exchange_color_list[0][0];
                int rank2 = exchange_color_list[0][1];
                if(one_card.getCardInt() % 4 == rank1){
                    one_card_str = Card::intCard2Str(one_card.getCardInt() - rank1 + rank2);
                }else if(one_card.getCardInt() % 4 == rank2){
                    one_card_str = Card::intCard2Str(one_card.getCardInt() - rank2 + rank1);
                }

            }

            // 计算发牌后的新 reach_probs（排除与新牌冲突的 combo）
            vector<vector<float>> new_reach_probs = reach_probs;
            if(this->enable_range) {
                uint64_t card_long = Card::boardInt2long(one_card.getCardInt());
                for(int player = 0; player < 2; player++) {
                    // 使用 reach_probs 的实际大小，而不是 ranges 的大小
                    std::size_t hand_count = std::min(reach_probs[player].size(), this->ranges[player].size());
                    for(std::size_t hand_id = 0; hand_id < hand_count; hand_id++) {
                        uint64_t privateBoardLong = this->ranges[player][hand_id].toBoardLong();
                        if(Card::boardsHasIntercept(card_long, privateBoardLong)) {
                            new_reach_probs[player][hand_id] = 0;
                        }
                    }
                }
            }

            this->reConvertJson(childerns,dealcards,one_card_str,depth + 1,max_depth,new_prefix,new_deal,new_exchange_color_list,new_reach_probs);
        }
        if((*retval)["dealcards"].empty()){
            (*retval).erase("dealcards");
        }

        (*retval)["deal_number"] = dealcards.size();
        (*retval)["node_type"] = "chance_node";
    }else{
        throw runtime_error("node type unknown!!");
    }
}

long long PCfrSolver::countNodes(const shared_ptr<GameTreeNode>& node, int depth, int max_depth) {
    if(depth >= max_depth) return 0;
    long long count = 0;
    
    if(node->getType() == GameTreeNode::GameTreeNodeType::ACTION) {
        shared_ptr<ActionNode> action_node = std::dynamic_pointer_cast<ActionNode>(node);
        count = 1;  // 计算当前节点
        for(auto& child : action_node->getChildrens()) {
            count += countNodes(child, depth, max_depth);
        }
    } else if(node->getType() == GameTreeNode::GameTreeNodeType::CHANCE) {
        shared_ptr<ChanceNode> chance_node = std::dynamic_pointer_cast<ChanceNode>(node);
        // Chance 节点会展开多张牌
        count = countNodes(chance_node->getChildren(), depth + 1, max_depth) * chance_node->getCards().size();
    }
    return count;
}

void PCfrSolver::printProgress(long long current, long long total, const std::string& prefix) const {
    if(total == 0) return;
    
    int bar_width = 40;
    float progress = (float)current / total;
    int pos = (int)(bar_width * progress);
    
    cout << "\r" << prefix << "[";
    for(int i = 0; i < bar_width; ++i) {
        if(i < pos) cout << "=";
        else if(i == pos) cout << ">";
        else cout << " ";
    }
    cout << "] " << int(progress * 100.0) << "% (" << current << "/" << total << ")";
    cout.flush();
}

json PCfrSolver::dumps(bool with_status,int depth) {
    if(with_status == true){
        throw runtime_error("");
    }
    
    // 统计节点数并初始化进度
    cout << "Counting nodes..." << flush;
    this->dump_total = countNodes(this->tree->getRoot(), 0, depth);
    this->dump_progress = 0;
    cout << " found " << this->dump_total << " action nodes" << endl;
    
    // 初始化 reach_probs（两个玩家的初始 range）
    vector<vector<float>> initial_reach_probs = this->getReachProbs();
    
    json retjson;
    this->reConvertJson(this->tree->getRoot(),retjson,"",0,depth,vector<string>({"begin"}),0,vector<vector<int>>(),initial_reach_probs);
    
    // 完成进度条
    printProgress(dump_total, dump_total, "Generating: ");
    cout << endl;
    
    return std::move(retjson);
}

