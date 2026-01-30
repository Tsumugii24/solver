//
// Created by Xuefeng Huang on 2020/1/31.
//

#include "trainable/CfrPlusTrainable.h"
#include <cmath>

CfrPlusTrainable::CfrPlusTrainable() {

}

CfrPlusTrainable::CfrPlusTrainable(shared_ptr<ActionNode> action_node, vector<PrivateCards> privateCards) {
    this->action_node = action_node;
    this->privateCards = privateCards;
    this->action_number = action_node->getChildrens().size();
    this->card_number = privateCards.size();

    this->evs = vector<float>(this->action_number * this->card_number, 0.0);
    this->equities = vector<float>(this->action_number * this->card_number, 0.0);
    this->r_plus = vector<float>(this->action_number * this->card_number, 0.0);
    this->r_plus_sum = vector<float>(this->card_number, 0.0);

    this->cum_r_plus = vector<float>(this->action_number * this->card_number, 0.0);
    this->cum_r_plus_sum = vector<float>(this->card_number, 0.0);
    this->retval = vector<float>(this->action_number * this->card_number, 0.0);
}

bool CfrPlusTrainable::isAllZeros(vector<float> input_array) {
    for(float i:input_array){
        if (i != 0)return false;
    }
    return true;
}

const vector<float> CfrPlusTrainable::getAverageStrategy() {
    /*
    vector<float> retval(this->action_number * this->card_number);
    if(this->cum_r_plus_sum.empty() || this->isAllZeros(this->cum_r_plus_sum)){
        fill(retval.begin(),retval.end(),1.0 / this->action_number);
    }else {
        for (int action_id = 0; action_id < action_number; action_id++) {
            for (int private_id = 0; private_id < this->card_number; private_id++) {
                int index = action_id * this->card_number + private_id;
                if(this->cum_r_plus_sum[private_id] != 0) {
                    retval[index] = this->cum_r_plus[index] / this->cum_r_plus_sum[private_id];
                }else{
                    retval[index] = 1.0 / this->action_number;
                }
            }
        }
    }
    */
    return this->getcurrentStrategy();
}

const vector<float> CfrPlusTrainable::getcurrentStrategy() {
    if(this->r_plus_sum.empty()){
        fill(retval.begin(),retval.end(),1.0 / this->action_number);
    }else {
        for (int action_id = 0; action_id < action_number; action_id++) {
            for (int private_id = 0; private_id < this->card_number; private_id++) {
                int index = action_id * this->card_number + private_id;
                if(this->r_plus_sum[private_id] != 0) {
                    retval[index] = this->r_plus[index] / this->r_plus_sum[private_id];
                }else{
                    retval[index] = 1.0 / this->action_number;
                }
                if(this->r_plus[index] != this->r_plus[index]) throw runtime_error("nan found");
                /*
                if(this.r_plus_sum[private_id] == 0)
                {
                    System.out.println("Exception regret status, r_plus_sum == 0:");
                    System.out.println(String.format("r plus length %s , card num %s",r_plus.length,this.card_number));
                    for(int i = index % this.card_number;i < this.r_plus.length;i += this.card_number){
                        System.out.print(String.format("%s:%s ",i,this.r_plus[i]));
                        if(i == index){
                            System.out.print("[current]");
                        }
                    }
                    System.out.println();
                    System.out.println();
                    throw new RuntimeException();
                }
                 */
            }
        }
    }
    return retval;
}

void CfrPlusTrainable::setEv(const vector<float>& evs){
    if(evs.size() != this->evs.size()) throw runtime_error("size mismatch in cfrplustrainable setEV");
    for(std::size_t i = 0;i < evs.size();i ++) if(evs[i] == evs[i])this->evs[i] = evs[i];
}

void CfrPlusTrainable::setEquity(const vector<float>& equities){
    if(equities.size() != this->equities.size()) throw runtime_error("size mismatch in cfrplustrainable setEquity");
    for(std::size_t i = 0;i < equities.size();i ++) if(equities[i] == equities[i])this->equities[i] = equities[i];
}

void CfrPlusTrainable::copyStrategy(shared_ptr<Trainable> other_trainable){
    shared_ptr<CfrPlusTrainable> trainable = dynamic_pointer_cast<CfrPlusTrainable>(other_trainable);
    this->r_plus.assign(trainable->r_plus.begin(),trainable->r_plus.end());
    this->cum_r_plus.assign(trainable->cum_r_plus.begin(),trainable->cum_r_plus.end());
}

void CfrPlusTrainable::updateRegrets(const vector<float>& regrets, int iteration_number, const vector<float>& reach_probs) {
    this->regrets = regrets;
    if(regrets.size() != this->action_number * this->card_number) throw runtime_error("length not match");

    //Arrays.fill(this.r_plus_sum,0);
    fill(r_plus_sum.begin(),r_plus_sum.end(),0);
    fill(cum_r_plus_sum.begin(),cum_r_plus_sum.end(),0);
    for (int action_id = 0;action_id < action_number;action_id ++) {
        for(int private_id = 0;private_id < this->card_number;private_id ++){
            int index = action_id * this->card_number + private_id;
            float one_reg = regrets[index];

            // 更新 R+
            this->r_plus[index] = max((float)0.0,one_reg + this->r_plus[index]);
            this->r_plus_sum[private_id] += this->r_plus[index];

            // 更新累计策略
            this->cum_r_plus[index] += this->r_plus[index] * iteration_number;
            this->cum_r_plus_sum[private_id] += this->cum_r_plus[index];
        }
    }
}

json CfrPlusTrainable::dump_strategy(bool with_state) {
    if(with_state) throw runtime_error("state storage not implemented");

    json strategy;
    vector<float> average_strategy = this->getcurrentStrategy();
    vector<GameActions> game_actions = action_node->getActions();
    vector<string> actions_str;
    for(GameActions one_action:game_actions) actions_str.push_back(
                one_action.toString()
        );

    //SolverEnvironment se = SolverEnvironment.getInstance();
    //Compairer comp = se.getCompairer();

    for(int i = 0;i < this->privateCards.size();i ++){
        PrivateCards one_private_card = this->privateCards[i];
        vector<float> one_strategy(this->action_number);

        /*
        int[] initialBoard = new int[]{
                Card.strCard2int("Kd"),
                Card.strCard2int("Jd"),
                Card.strCard2int("Td"),
                Card.strCard2int("7s"),
                Card.strCard2int("8s")
        };
        int rank = comp.get_rank(new int[]{one_private_card.card1,one_private_card.card2},initialBoard);
         */

        for(int j = 0;j < this->action_number;j ++){
            int strategy_index = j * this->privateCards.size() + i;
            // 保留3位小数
            one_strategy[j] = round(average_strategy[strategy_index] * 1000.0f) / 1000.0f;
        }
        strategy[fmt::format("{}",one_private_card.toString())] = one_strategy;
    }

    json retjson;
    retjson["actions"] = actions_str;
    retjson["strategy"] = strategy;
    return retjson;
}

json CfrPlusTrainable::dump_evs() {
    json evs;
    const vector<float>& average_evs = this->evs;
    vector<GameActions> game_actions = action_node->getActions();
    vector<string> actions_str;
    for(GameActions one_action:game_actions) {
        actions_str.push_back(one_action.toString());
    }

    for(std::size_t i = 0;i < this->privateCards.size();i ++){
        PrivateCards one_private_card = this->privateCards[i];
        vector<float> one_evs(this->action_number);

        for(int j = 0;j < this->action_number;j ++){
            std::size_t evs_index = j * this->privateCards.size() + i;
            // 保留2位小数
            one_evs[j] = round(average_evs[evs_index] * 100.0f) / 100.0f;
        }
        evs[fmt::format("{}",one_private_card.toString())] = one_evs;
    }

    json retjson;
    retjson["actions"] = std::move(actions_str);
    retjson["evs"] = std::move(evs);
    return std::move(retjson);
}

json CfrPlusTrainable::dump_equities() {
    json equities;
    const vector<float>& average_equities = this->equities;
    vector<GameActions> game_actions = action_node->getActions();
    vector<string> actions_str;
    for(GameActions one_action:game_actions) {
        actions_str.push_back(one_action.toString());
    }

    for(std::size_t i = 0;i < this->privateCards.size();i ++){
        PrivateCards one_private_card = this->privateCards[i];
        vector<float> one_equities(this->action_number);

        for(int j = 0;j < this->action_number;j ++){
            std::size_t equities_index = j * this->privateCards.size() + i;
            // 保留3位小数
            one_equities[j] = round(average_equities[equities_index] * 1000.0f) / 1000.0f;
        }
        equities[fmt::format("{}",one_private_card.toString())] = one_equities;
    }

    json retjson;
    retjson["actions"] = std::move(actions_str);
    retjson["equities"] = std::move(equities);
    return std::move(retjson);
}

Trainable::TrainableType CfrPlusTrainable::get_type() {
    return CFR_PLUS_TRAINABLE;
}
