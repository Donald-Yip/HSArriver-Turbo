# 动物园术（标准 / 甲虫年）卡组策略：铺场术。
#
# 卡组代码：AAECAcn1AgLb4Aft+QcO0J4G15cHhJkHoq0HpK0HqK0Hk74H9sEHgfYHivYHjfYH6fkH6/kHpIEIAAA=
# 打法思路见同目录 动物园术.txt：
#   前期把每回合法力转成场面 -> 群体增益（续连熵能/漂浮教程/庭审+3+3）
#   -> 污亵返场 -> 灾毁迅疾幼龙（牌库有撕裂时）一回合两个 5/5 突袭。
#
# 评分框架与快牧一致：
#   rankState(state, actions) = rankStaticState(state) + rankActions(state, actions)
# rankActions 评最后一个动作，并补上序列里“效果没被搜索模拟”的早前动作
# （法术 / 英雄技能 / 幸运币）；顺序修正读整个 actions 列表。
#
# 认牌一律用 card_id（用户要求），卡名只出现在注释与调试日志里。
from types import SimpleNamespace

from src.recommendation_models import ActionKind
from search.ai_action import AiAction, AiSlot
from search.ai_state import AiState
from src.game_state.recommendation_adapter import ordered_board
from ai.card_rules import (
    DRAWN_CARD_PLAY_RESERVE_MANA, ability_spec, defile_killed_card_ids)

from .dwys_context import (
    CARD_ATTRIBUTES,
    CARD_ID_CRYPT,
    CARD_ID_CULT_PRIEST,
    CARD_ID_CULT_PRIEST_CORE,
    CARD_ID_CUTHUN,
    CARD_ID_CUTHUN_DOMAIN,
    CARD_ID_CUTHUN_FURY,
    CARD_ID_CUTHUN_GAZE,
    CARD_ID_CUTHUN_RITUAL,
    CARD_ID_CUTHUN_SERVANT,
    CARD_ID_DEFILE,
    CARD_ID_DRAKE,
    CARD_ID_EGG,
    CARD_ID_ENTROPY,
    CARD_ID_GLACIER,
    CARD_ID_GLACIER_LEGACY,
    CARD_ID_HERALD,
    CARD_ID_KAZAKUS,
    CARD_ID_KISHIX,
    CARD_ID_NZOTH,
    CARD_ID_PROTOTYPE,
    CARD_ID_PTERRORDAX,
    CARD_ID_RITUALIST,
    CARD_ID_SAVIS,
    CARD_ID_SIN_CONQUEST,
    CARD_ID_SIN_DOMINION,
    CARD_ID_SIN_FAMINE,
    CARD_ID_SOLDIER,
    CARD_ID_SUMMON_RITUAL,
    CARD_ID_TIMEWARP,
    CARD_ID_TRIAL_BLADE,
    CARD_ID_TRIAL_CONSPIRE,
    CARD_ID_TRIAL_CONTRABAND,
    CARD_ID_TRIAL_DETAIN,
    CARD_ID_TRIAL_ENDLESS,
    CARD_ID_TRIAL_ENDLESS_OPTION,
    CARD_ID_TRIAL_FAST,
    CARD_ID_TRIAL_FAST_OPTION,
    CARD_ID_TRIAL_FORGERY,
    CARD_ID_TRIAL_PACT,
    CARD_ID_TRIAL_POTION,
    CARD_ID_TRIAL_SLOW,
    CARD_ID_TRIAL_SLOW_OPTION,
    CARD_ID_TRIAL_SMUGGLE,
    CARD_ID_TRIAL_TYRANT,
    CARD_ID_TUTORIAL,
    CARD_ID_YOGG_SARON,
    CARD_ID_YSHARAJ,
    CARD_ID_ZAKAZ,
    CARD_IDS_KELTHUZAD_EGG,
    CARD_ID_VAULT_GUARD,
    CUTHUN_ABILITY_COUNT,
    CUTHUN_ABILITY_MAX_PER_TURN,
    CUTHUN_ABILITY_ORDER,
    TRIAL_EFFECT_PRIORITY,
    DwysDeckContext,
    card_item,
    card_name,
    card_text,
    item_flag,
    item_stats,
)

# ---------------------------------------------------------------- 评分常量
ATTACK_WEIGHT = 2.0
HEALTH_WEIGHT = 1.0
MY_HERO_WEIGHT = 1.0
ENEMY_HERO_WEIGHT = 1.0
# 敌方场面威胁（2026-10-25 复盘结论）：敌方随从下回合能打出的伤害要计入静态
# 场面分，只统计攻击力 >= THREAT_HIGH_ATTACK 的高威胁随从（按全额攻击力计）。
# 威胁只在“我方血量进入危险区”时才生效：血量 >= THREAT_URGENCY_HEALTH 时
# 不计威胁（高血量下人类会继续打脸抢节奏，盒子AI 样本 t010-s2），血量越低
# 线性放大，0 血时按 1 倍攻击力计。这样既能在被斩杀风险区少留大场面，
# 又不会因为对面一只 5 攻怪就放弃打脸。
THREAT_HIGH_ATTACK = 5
THREAT_HIGH_WEIGHT = 1.0
THREAT_URGENCY_HEALTH = 20.0

TAUNT_VALUE = 2.0
REBORN_VALUE = 3.0
DIVINE_SHIELD_VALUE = 2.0
RUSH_VALUE = 2.0
LIFESTEAL_VALUE = 3.0
POISONOUS_VALUE = 3.0
STEALTH_VALUE = 1.0

LETHAL_BONUS = 1000.0
# 克苏恩，帝国之眼本体技能（用户 2026-10-26 简化口径）：除非用不了，默认每回合
# 用满 2 次，尽快把 4 个技能烧完——4 个技能全点亮之前本体（12/12）不能进攻，
# 早一回合能动就是巨大收益。每次激活给大额固定加分，保证搜索优先走激活分支；
# 同回合第 2 次再额外加一档，避免只点一次就收手。技能之间的差异只给随机
# 技能（愤怒/领域）补期望值，弥补 doAction 的确定化模拟误差；凝视/仆从由
# doAction 精确模拟，最终场面分已经算过，动作分不再重复加分（agents.md 原则 4）。
CUTHUN_ABILITY_ACTIVATE_BONUS = 60.0
CUTHUN_ABILITY_SECOND_USE_BONUS = 40.0
# 本体还有“可用但没用”的技能、本回合也还能再激活时，结束回合重扣：
# 「提前收工」不能比「多烧一个技能」更划算。只在确实还有可用技能时生效，
# 避免没有合法技能时把结束回合压到比乱出牌还低。
CUTHUN_ABILITY_END_TURN_PENALTY = -80.0
# 英雄技能·生命分流的加分统一在序列级规则 _hero_power_draw_bonus 里算
# （用户 2026-10-29：原来逐动作的 _hero_power_gain 和这条规则各加一份，同一个
# 分流被记成 +2 和 +9；现在只有这一处给分，逐动作的 _hero_power_gain 只留
# “血量不安全就别用”的扣分）。
#   1) 抽牌价值：按“序列开始时”的场面挑一个最强理由，不叠加；
#   2) 本回合第一个动作用技能：再 +1（抽到的牌能改变后续选择）。
# 第 1 条必须只看开局场面，不能看“分流排在第几位”：同一个分流换个顺序就
# 一会儿有分一会儿没分，是 ann-20261024_062909 t014-s1 里“先分流该 10 分、
# 先打脸再分流该 9 分”对不上的原因。
HERO_POWER_FIRST_ACTION_BONUS = 1.0
HERO_POWER_THREAT_ATTACK = 10      # 敌方单只随从攻击力达到这个数 = 超级威胁
HERO_POWER_THREAT_BONUS = 25.0     # 手里没解：抽牌找答案
HERO_POWER_FULL_BOARD_BONUS = 6.0  # 场面 7 格铺满：抽牌找群体增益
# 我方空场、手里又没有足够的随从可铺：抽牌找铺场资源（用户 2026-10-23 标注
# t012-s0「空场坚决不出续连熵能，这时应该抽牌，看有没有比手牌更好的选择」）。
HERO_POWER_EMPTY_BOARD_BONUS = 8.0
HERO_POWER_EMPTY_BOARD_HAND_MINIONS = 1
# 同名手牌优先出右边那张（用户 2026-10-23）：栉龙 TLC_603 的亡语弃掉的是
# 战吼抽到的那张牌（新抽到的牌总在最右边），两张同名牌价值相同时先出右边
# 那张，等于先把“可能被弃掉”的那张用掉。只做同分打破，量级必须很小。
SAME_CARD_RIGHT_PENALTY = 0.5

# 攻击力溢出同分打破（用户 2026-10-24 标注 t008-s3）：高攻随从的攻击力要
# 留给英雄或更大的目标，不该浪费在血更少的随从上。同样能换掉目标时，优先
# 用“攻击力刚好够用”的那只：3 攻打 3 血 = 0 溢出，4 攻打 3 血浪费 1 点。
# 0.01/点远小于任何实质收益差（收益都是整数），只破同分，不改变收益比较。
OVERKILL_TIEBREAK = 0.01
TEAR_PENALTY = 0.4  # 因为抽到的概率很低，数学期望较低
TEAR_LOW_HEALTH_PENALTY = 3.0
# 咒怨之墓（TLC_451）发现的牌是临时牌，回合结束没打出就会被丢弃。
# 用户 2026-10-28 把这条规则从“留在手里扣分”改成“打出去加分”：
# 本回合把它打出去 = 救下这张牌，+20（要能压过教父卡扎库斯 21.5 的筹备分，
# 否则临时牌喉魔异教徒会被卡扎库斯顶掉，见 case_0015）。留在手里不再扣分。
CRYPT_TEMP_PLAY_BONUS = 20.0
# 发现菜单来自咒怨之墓（临时来源）时，选“本回合付得起”的选项额外 +12：
# 选一张打不出去的临时牌等于白扔一次发现。原来这里是 -30，按用户要求
# 统一改成正向分数（2026-10-28）；付不起的选项直接按 0 计（见
# _generic_discover_gain），不引入负分。
CRYPT_CASTABLE_BONUS = 12.0
# 污亵 BE_086：卡面“如果本牌为你手牌中法力值消耗最高的牌，重复一次”。
# 不是最高费时只结算 2 点（不是 4 点），打出去收益缩水
# （用户 2026-10-28：不是最高费用时打出污亵扣 1 分）。
DEFILE_NOT_TOP_PENALTY = -1.0
# 敌方场上一个随从都没有：没有人能反打/换掉我方场面。清空的收益按“动作
# 序列开始前对方场面的敌方随从数量”结算，上限 5 分
# （用户 2026-10-29）。
ENEMY_EMPTY_BOARD_BONUS_CAP = 5.0
# 1 攻随从打脸只值 1 点伤害，不值得为了它放弃换怪/占场（用户 2026-10-24
# 标注 t014-s1：1 攻怪打脸不该多拿分）。
LOW_ATTACK_FACE_CAP = 0.5
# 我方嘲讽随从不必主动换掉敌方低威胁随从：对方会来撞嘲讽，3 攻以下的
# 非嘲讽随从威胁不大（用户 2026-10-24 标注 t008-s5）。
TAUNT_LOW_THREAT_ATTACK = 3
# 嘲讽随从主动去换敌方低攻非嘲讽随从：-4（对方会来撞嘲讽，低攻随从威胁不大）。
# 目标有持续收益（饱胀水蛭等）必须解掉时豁免，见 _attack_gain。
TAUNT_LOW_THREAT_PENALTY = -4.0
# 凯洛斯的蛋（用户 2026-10-24）：打掉一枚蛋只会让它更快破壳，最后孵出
# 20/20 嘲讽。未破壳（非嘲讽）的蛋留在对面反而是好事：按“活着有利”加分，
# 任何攻击/单体法术指向它都重罚（除非它已经长出嘲讽）。
EGG_ALIVE_BONUS = 8.0
ATTACK_EGG_PENALTY = -100.0
# 库区守卫 BE_151（用户 2026-10-24）：战吼/亡语给相邻随从 +2/+2，所以
# 必须放在两侧都有随从的内侧空隙，且优先贴大随从、或贴“+2 攻后正好能
# 解掉敌方随从”的随从。+2/+2 折现 6 分/邻（攻 +2 = 4 分，血 +2 = 2 分），
# 亡语不一定触发，整体再打折。
VAULT_GUARD_BUFF_BASE = 6.0
VAULT_GUARD_BIG_MINION_BONUS = 0.3
VAULT_GUARD_TRADE_BONUS = 4.0
VAULT_GUARD_BUFF_DISCOUNT = 0.7
VAULT_GUARD_BUFF_CAP = 18.0
# 本回合必须落场的随从：提前打出时把自身收益补在动作上，否则收益只在
# 最后一张才结算，搜索会把它们一直拖到回合末（t006-s0 龙卵、t016-s2 幼龙）。
#   栉龙 TLC_603：战吼抽的牌要当回合用掉，法力用尽时不该出（t006-s3、
#     t008-s0，用户 2026-10-24）；提前补分才能让“0 费不出”的惩罚生效。
#   库区守卫 BE_151：+2/+2 只加给已站场随从，越晚出收益越小（t011-s0）。
#   教父卡扎库斯 CAP_405：战吼“筹备庭审”的收益本来只在最后一张动作结算，
#     排到回合末才能拿满 32 分，于是搜索宁可先分流/先攻击再出它（101941
#     t010-s3：人类要求临时牌卡扎库斯先落场）。提前打出时同样补上筹备分，
#     与“关键牌必须尽早落场”的口径一致（2026-10-25）。
URGENT_PLAY_CARD_IDS = frozenset({
    CARD_ID_EGG, CARD_ID_DRAKE, CARD_ID_PTERRORDAX, CARD_ID_VAULT_GUARD,
    CARD_ID_KISHIX, CARD_ID_KAZAKUS})
DISCOVER_UNKNOWN = 0.0
# 幸运币：只值得用来凑出 3 费及以上的牌（用户 2026-10-23：硬币留给出
# 3 费怪/4 费怪，不值得跳币出异教低阶牧师）。召唤仪式 BE_085 是明确被
# 标注要求跳币出的 2 费例外。凑不出这些牌还硬打硬币，等于白丢一张牌。
# 具体值多少由 _coin_gain 按“剩余法力 + 1 能出的牌”估值决定。
COIN_ENABLED_MIN_COST = 3
COIN_WASTE_PENALTY = -6.0
# 咒怨之墓：手里有 3 费及以上的牌可出时先出牌（用户 2026-10-23：
# “一般是手牌没有太好的选择或者想找特定的牌时再出”）。
CRYPT_STRONG_HAND_PENALTY = -20.0
# 咒怨之墓：0 法力打出时发现的牌是临时牌，这回合根本打不出去，等于白扔
# 一张咒怨之墓（用户 2026-10-24 标注 t008-s2：宁可直接结束回合）。
CRYPT_NO_MANA_PENALTY = -100.0
# 教父卡扎库斯 CAP_405：战吼只是“筹备庭审”，真正的收益（两个庭审效果）
# 要等庭审牌打出去才结算，搜索模拟不到。这里把“下回合最好两个效果”的
# 价值按 0.5 折现加到出牌分上（用户 2026-10-23 标注 t006-s0：3 费回合
# 有卡扎库斯时先出它，而不是先打咒怨之墓/先铺 2+1 费）。
# 封顶 20（用户 2026-10-24）：庭审衍生物很强，3 费回合卡扎库斯要能压过
# “牧师 + 咒怨之墓 + 龙卵”的铺场线（case_0014 标注）。
KAZAKUS_SETUP_DISCOUNT = 0.5
KAZAKUS_SETUP_CAP = 12  # 基于样例ann-20261026_133421_动物园术-t005-s0 要比不出它好
# 攻击顺序：先把法力转成场面，攻击放到最后（用户 2026-10-23 标注
# t006-s0：3 费回合先出教父卡扎库斯）。搜索按“最后动作执行前的场面”计分，
# 免费的攻击放在前面相当于白送 4 分，任何“某张牌第一步”都会天然矮一头；
# 这里对“攻击之后还跟着出牌动作”的序列扣分，把顺序拉回人类习惯。
ATTACK_BEFORE_PLAY_PENALTY = -6.0
# 关键牌必须尽早落场（用户 2026-10-24 确认“关键牌后置扣分”）：
#   * 教父卡扎库斯 CAP_405：庭审筹备越早，下回合的庭审牌越早生效；
#   * 库区守卫 BE_151：+2/+2 只加给已经站场的随从，越晚出收益越小；
#   * 栉龙 TLC_603：战吼抽到的牌要当回合用掉，越早出越有余费。
# 它们出现在序列里、却不是第一张打出的牌时整条序列扣分。硬币只是凑费，
# 不算抢占关键牌的出场时机（t006-s0 期望跳币后出牌）。
KEY_FIRST_CARD_IDS = frozenset({
    CARD_ID_KAZAKUS, CARD_ID_VAULT_GUARD, CARD_ID_PTERRORDAX})
KEY_CARD_LATE_PENALTY = -20.0
# 临近斩杀：敌方英雄血量 1-15、敌方随从都不构成威胁（无嘲讽、攻击<=3）、
# 且序列开始时手里没有 >=3 费可出的牌时，攻击敌方英雄补分。
# 只作为同分打破：打脸的伤害本身已经由 _attack_gain / 后续静态分计过，
# 这里不再按次数放大（agents.md 原则 4：动作分只补场面看不到的未来价值）。
FACE_DAMAGE_BONUS = 2.0
FACE_DAMAGE_MAX_HEALTH = 15
HARMLESS_ATTACK_MAX = 3
# 场面压力线（用户 2026-10-25 标注 t008-s0）：对方场攻 + 随从数量 >= 10，
# 且挨完这轮场攻我方血量会掉到 <= 15，就算“必须立刻清场”，这时不能再出
# 延迟收益的庭审牌（见 _must_clear_now）。
CLEAR_PRESSURE_SUM = 10
CLEAR_PRESSURE_HEALTH = 15
# 突袭随从解敌方嘲讽：嘲讽解掉后其余随从才能打脸，按人类思路补分
# （用户 2026-10-25 确认 t009-s1：突袭 5 攻 + 1 号随从 1 攻正好解 6 血嘲讽）。
# 只在后面还有动作时生效（见 rankActions）：最后一个攻击没有“开路”价值。
RUSH_CLEAR_TAUNT_BONUS = 10.0
# 发现菜单里的突袭/冲锋随从：选它本回合就能落地直接解场，属于“抢节奏”。
# （用户 2026-10-25 确认 205200 t011-s1：三选一里人类放弃价值更高的卡扎库斯，
#   选 5 费 5/5 突袭幼龙，理由就是抢节奏/立即清场。）
# 按当回合能撞死的敌方随从价值补分：撞完自己活下来算目标全价，同归于尽只
# 算双方差额（为负不算）。不额外给固定分，避免“没东西可解也加分”。
RUSH_DISCOVER_TEMPO = 1.0
# 漂浮教程：使一个随从和手牌中一张随机随从各获得「2×本牌法力消耗」属性值。
# 2026-10-25 起按卡面效果折算（用户确认）：每个目标 6 分/费（攻 2 分、血 1 分）。
# 1 费出仍按原口径扣分（留着等加费更值）。
# 注：1 费扣分再狠（实际收益 -4 及以下）会让 t015-s4 的顺序翻掉——人类那步是
# “先打脸、再出 1 费漂浮教程”，扣多了搜索会改成先出牌，第一步就对不上。
TUTORIAL_BOARD_TARGET_PER_COST = 6.0
TUTORIAL_EARLY_PENALTY = 10.0
# 手牌那一半是“随机一张”，收益要按手牌随从数量取平均：本回合会真正打出去的
# 那张按全额（属性直接变成场面分），其余只按 TUTORIAL_HAND_BUFF_RATE 兑现
# （属性还在手牌上，本回合变不成场面）。这样「先出幼龙、再贴教程」会因为随机
# buff 只能落到本回合出不起的萨维斯身上而自然掉分，不再靠固定加分。
# （111710 t011-s0 人类备注 + 用户 2026-10-25 修正：判断教程收益要引入 buff
# 手牌随从的平均收益。）
TUTORIAL_HAND_BUFF_RATE = 0.3
# 喉魔光环把教程抬高 1 费 = 两个目标各多 +2/+2，折合约 12 分：先上喉魔再贴
# 教程能多换这一档，反之先贴就白丢（见 _tutorial_before_ritualist_penalty）。
TUTORIAL_AURA_EXTRA = 12.0
# 末日预言者 CORE_NEW1_021 / NEW1_021：对手回合开始时消灭所有随从。
# 本回合杀不掉时，任何站场投资（出随从、只给随从加属性的法术）都会白送；
# 抽牌/发现/英雄技能不受影响（用户 2026-10-25 标注 t006-s0）。
CARD_IDS_DOOMSAYER = frozenset({"CORE_NEW1_021", "NEW1_021"})
DOOMSAYER_WASTE_PENALTY = -25.0
DOOMSAYER_KILL_BONUS = 20.0
# 敌方“每回合持续收益”的随从必须解掉（用户 2026-10-25 确认 101941 t008-s1 /
# t010-s7）：除非这回合能斩杀，否则留一回合就白送对面一份收益。饱胀水蛭
# EDR_810t 的卡面不是吸血关键字，而是“回合结束偷 1 点生命”，日志也只带
# TRIGGER_VISUAL，所以只能按 card_id 认；带 life_steal 标记的一并算。
DRAIN_MINION_CARD_IDS = frozenset({"EDR_810", "EDR_810t"})
DRAIN_MINION_KILL_BONUS = 12.0
# 优先要解的敌方卡牌（用户 2026-10-25 确认）：这些随从要么持续给全场加
# 攻击力，要么每回合结束对我方全场造成伤害，留着会滚雪球。
# 击杀得分改为 map（用户 2026-10-26）：默认 PRIORITY_KILL_BONUS（15 分），
# 表里写了数字的按数字给（如火炮手 6 分）。没击杀也按“已造成伤害 / 目标
# 生命”给蹭血分，方便本回合或下回合收掉（用户备注：哪怕解不掉，蹭血也是好的）。
PRIORITY_KILL_BONUS = 15.0
PRIORITY_KILL_SCORES = {
    "CATA_475": 30,     # 破鳞盾卫：回合结束对所有敌人造成 2 点伤害 ann-20261025_211619_动物园术-t009-s0
    "CATA_565t": PRIORITY_KILL_BONUS,    # 奥拉基尔的士兵：相邻的随从拥有 +1 攻击力
    "CATA_473": PRIORITY_KILL_BONUS,     # 诺兹多姆，青铜守护巨龙
    "BT_187": PRIORITY_KILL_BONUS,       # 凯恩·日怒
    "CORE_BT_187": PRIORITY_KILL_BONUS,  # 凯恩·日怒
    "BE_013": PRIORITY_KILL_BONUS,       # 克苏恩，帝国之眼
    "JAIL_850": PRIORITY_KILL_BONUS,     # 典狱官玛维
    "JAIL_802": PRIORITY_KILL_BONUS,     # 加乐宫恶棍
    "BE_017": PRIORITY_KILL_BONUS,       # 恩佐斯，梦境之末
    CARD_ID_YOGG_SARON: PRIORITY_KILL_BONUS,  # 古神主谋尤格-萨隆
    CARD_ID_YSHARAJ: PRIORITY_KILL_BONUS,     # 亚煞极，泰坦杀神
    "CAP_104": PRIORITY_KILL_BONUS,      # 炸药工程师
    "CAP_106": PRIORITY_KILL_BONUS,      # 克罗雷船长
    "CAP_000": PRIORITY_KILL_BONUS,      # 军情七处杀手（ann-20261028_204524 t006-s0）
    "CAP_003": 10,      # 军情七处补给官（同上）
    "CATA_474": PRIORITY_KILL_BONUS,     # 矛心哨卫
    "CAP_107t": 6.0,   # 火炮手（战士）：回合结束随机对一个敌人造成 1 点伤害
}
PRIORITY_CHIP_BONUS = 10.0
# 只给随从加属性、不产生其他场面的法术：末日预言者杀不掉时收益全废。
BOARD_BUFF_SPELL_IDS = frozenset({CARD_ID_TUTORIAL, CARD_ID_ENTROPY})
# 腐蚀者扎卡兹 BE_728：减费折现；手里有漂浮教程时，
# 扎卡兹的法术 +1 费正好把教程抬大一档，额外 +1.5 分（用户 2026-10-25）。
ZAKAZ_TUTORIAL_BONUS = 1.5
# 减费价值：手牌随从各 -1 费 = 各多 1 点可支配法力，按 1 费约 2分折现
# （用户 2026-10-25 确认：扎卡兹不只看自己身材，也要看减费价值）。
# 额外给“减完正好能出”“减到 0 费随时能甩”的随从加一点。
ZAKAZ_DISCOUNT_PER_MINION = 2.0
ZAKAZ_DISCOUNT_UNLOCK_BONUS = 2.0
ZAKAZ_DISCOUNT_FREE_BONUS = 2.0
ZAKAZ_DISCOUNT_CAP = 16.0
# 冰川裂片 CORE_UNG_205 / UNG_205：战吼冻结一个敌人。冻结只剥夺目标一回合
# 攻击，按“阻止的伤害”折现，目标攻击力越高越值钱；已冻结目标不再重复计分。
GLACIER_FREEZE_PER_ATTACK = 0.8
GLACIER_FREEZE_CAP = 10.0
GLACIER_FREEZE_HERO = 0.0
# 敌方英雄头上的关键武器（用户 2026-10-30 标注 105908 t009-s0）：触须刺鞭
# BE_771「在你的英雄攻击后，使克苏恩的法力值消耗减少（1）点。选择并触发一项
# 克苏恩的技能」——留着每回合都在赚。冻结敌方英雄 = 这一回合武器打不出去，
# 也就触发不了武器效果，按表给分；普通武器/空手仍然按 GLACIER_FREEZE_HERO。
WEAPON_FREEZE_SCORES = {
    "BE_771": 12.0,     # 触须刺鞭
}
# 冻住后敌方下回合可见场攻不足斩杀：这一冻正好救命，给一次大分。
GLACIER_PREVENT_LETHAL = 30.0
# 没有值得冻的目标（低攻随从 / 敌方英雄 / 已冻结）时，2/1 身材不值一张牌 + 1
# 点法力：整牌按“冻结收益 - 4”计，不再计身材分（用户 2026-10-25 确认，
# 101941 多例备注“冰川也没有值得冻的”）。
GLACIER_FREEZE_USELESS = 3.0
GLACIER_USELESS_PENALTY = - 10.0  # 扣掉比冰川自己的静态价值低
# 冻结敌方最大攻击随从、保住我方场面，配合“我方下回合场攻足够斩杀”：
# 这一冻是斩杀前的保命（111710 t013-s3 人类备注）。
GLACIER_PROTECT_LETHAL = 12.0
# 梦魇之王萨维斯 EDR_856：4/4 站场之外，战吼“牌库发现随从 + 黑暗之赐”
# 搜索不模拟结算，按一次手牌资源收益折现（发现约 +3，黑暗之赐约 +4）。
SAVIS_BATTLECRY_GAIN = 7.0
# 基希克斯 BE_090：完成过仪式时战吼开启一项罪孽仪式（2 回合后结算）。
# 用户 2026-10-25：「全卡组最强牌，满足条件后基础分可以高一些」——按最好
# 选项约 24 分全额计入，再加 4 分体现“最强调度”（旧版打 0.5 折现只有 10 分，
# 压不过幼龙的牌库分）。
KISHIX_RITUAL_SETUP = 21.0
# 重生的翼手龙 TLC_436：4/3 突袭吸血，卡面「消耗残骸而非法力值」——出它
# 不占本回合法力（111710 t014-s1 人类备注：用于解场，还不占用费用）。
# 搜索仍按日志里的费用记账，这里把省下的法力折回牌本身的分。
CARD_ID_REBORN_PTERRORDAX = "TLC_436"
# 腐蚀者扎卡兹 BE_728 的减费正好让“条件已满足的基希克斯”提前一回合上场
# （111710 t007-s1 人类备注：6 费基希克斯变 5 费，下回合 5 费就能出）。
ZAKAZ_KISHIX_EARLY_BONUS = 12.0
# 英雄技能抽牌博斩杀：本回合场攻打完敌方会落到 <= 15 血（还打不死）、我方
# 英雄血量 >=15、且序列开始时手里没有 >=3 费可出的牌时，用英雄技能过牌找
# AOE / 群体增益（用户 2026-10-25 确认 t013-s0 / t015-s0）。
# 2026-10-29 改成按“序列开始时的场面”估算剩余血量，与分流排第几位无关
# （ann-20261024_062909 t014-s1：敌方 19 血、我方场攻 16，先分流该 10 分）。
HERO_POWER_DRAW_BONUS = 8.0
HERO_POWER_DRAW_MAX_HEALTH = 15
HERO_POWER_DRAW_MIN_MY_HEALTH = 15
# 续连熵能斩杀：发现界面选择熵能（全场随从 +1/+1）后场攻总和足够斩杀时，
# 直接判为最优选项（用户 2026-10-25 确认 t019-s2）。
ENTROPY_LETHAL_BONUS = 1000.0
# 咒怨之墓第一个出：手里没有别的 >=3 费可出牌时，先出它才能留够费用做出
# 最佳发现（用户 2026-10-25 确认 t011-s0，首个动作不是它就扣分）。
CRYPT_FIRST_PENALTY = -20.0
CRYPT_FIRST_MIN_MANA = 3
# 咒怨之墓出完只剩不到 3 费时，发现的临时牌这回合基本打不出去（临时牌费用
# 普遍 2 费以上），等于白花一张牌（用户 2026-10-25 确认 111710 t001-s0：1 费
# 回合宁可直接出仪式）。惩罚分 a：0 费是必浪费，仍走 CRYPT_NO_MANA_PENALTY。
CRYPT_LOW_MANA_MAX = 2
# 只剩 ≤2 费时出咒怨之墓：发现的临时牌基本付不起，这一手大概率白费
# （用户 2026-10-28 统一口径：咒怨之墓与栉龙的“低法力出牌”都扣 12）。
CRYPT_LOW_MANA_PENALTY = -15.0
# 幸运币“凑出来”的那张牌至少要值这么多分才跳币，否则等于白丢一张牌
# （用户 2026-10-24：按剩余法力 +1 能出的牌的价值判断，不给固定奖励分）。
COIN_ENABLED_MIN_GAIN = 11.0
# 2 费回合跳硬币出 3 费卡扎库斯：下回合只剩 3 费，接不上 4 费庭审
# （CAP_405tb2），卡扎库斯的价值兑现不了，等于拿一张 2 费好牌（244 跃迁者、
# 召唤仪式）换它早一回合落地。惩罚按“硬币+卡扎库斯”整条线算，压到不跳币的
# 2 费线以下：244 跃迁者 15 分、召唤仪式 20 分都能赢过它
# （用户 2026-10-25 确认 160228 t004 及新增的跃迁者对照样例）。
COIN_KAZAKUS_TURN2_PENALTY = 36.0
# 打残但打不死：自己活着、对方剩 1-2 血，下回合随便一只随从就能收掉，
# 这种“留残血”的攻击值得做（用户 2026-10-24 标注 t007-s6：3/4 撞 3/5
# 自己没死，未来和对方 4/2 交换也不亏）。补一点分抵消换血损失。
CHIP_SETUP_HEALTH = 2
CHIP_SETUP_BONUS = 3.0
# 只造成 1 点伤害的“留残血”不值得：对方只掉 1 血，自己还要吃满对方一击
# （用户 2026-10-25 确认 125102 t008-s3：1/3 暮光龙卵撞 2/3 奖品商贩，
# 打不死对方，龙卵反而白掉 2 血）。伤害 ≥2 才算有效的铺垫。
CHIP_SETUP_MIN_DAMAGE = 2
# 亡语会对全体我方造成伤害的敌方随从：主动攻击会加速它死亡、把亡语提前
# 触发，等于自己清自己的场（用户 2026-10-25 确认 125102 t012-s2：下水道
# 小鬼 JAIL_007「亡语：对所有敌人造成 2 点伤害」，不许攻击）。
AOE_DEATHRATTLE_IDS = frozenset({"JAIL_007"})            # 下水道小鬼
AOE_DEATHRATTLE_ATTACK_PENALTY = -30.0
# 栉龙 TLC_603 战吼抽到的那张牌带“易丢弃”标记：它的亡语会把这张牌弃掉。
# 用户 2026-10-28 把这里从“留在手里扣分”改成“当回合打出去加分”：
# 本回合把抽到的那张牌打出去 = 救下这张牌，+15（与咒怨之墓同一口径）。
# 原来这里是留手 -25 的扣分，现已去掉；但“出栉龙后只剩 ≤2 费”仍扣 12
# （用户 2026-10-28：与咒怨之墓统一口径，抽到的牌当回合打不出去）。
PTERRORDAX_DRAWN_PLAY_BONUS = 15.0  
PTERRORDAX_LOW_MANA_PENALTY = -10.5
# 咒怨之墓 TLC_451 / 栉龙 TLC_603：抽到（发现）的那张牌在搜索模拟里看不见，
# 按“当回合花一次操作把它打出去”估值。法力侧由
# ai.card_rules.drawn_card_mana_reserve 在 doAction 里立刻预留 3 费，
# 出牌分这里补分；但打出后剩余法力必须 ≥ 这 3 费才给，否则那张牌这回合
# 根本打不出去，加分是凭空来的（用户 2026-10-30）。
DRAWN_CARD_PLAY_BONUS = 10.0
# 咒怨之墓是从牌库「三选一」（能挑更好的那张），栉龙只是随机抽一张；
# 同样满足剩余法力 ≥3 时，咒怨多给 4 分（用户 2026-10-30）。
CRYPT_DRAWN_CARD_PLAY_BONUS = 14.0

# 庭审 9 效果的同分优先级（t9 > t6 > t5 > t3 > t4 > t7 > t2 > t8 > t1）。
_TRIAL_PRIORITY = TRIAL_EFFECT_PRIORITY


class Dwys:
    """动物园术策略（纯静态方法，无实例状态）。"""

    # rankState 的新语义：传入的 state 是动作序列开始前的场面，
    # 策略内部模拟完整序列后再算终局静态分（用户 2026-10-26）。
    # 搜索框架据此决定是传根节点还是“最后一个动作之前”的节点。
    RANKS_FROM_SEQUENCE_START = True

    # ================================================================ 留牌
    @staticmethod
    def choose(state: AiState) -> list[int]:
        """起手留牌：返回需要【换掉】的手牌 0 基下标。

        规则见 txt 第五节（2026-10-25 更新）：
          * 必留：克苏恩仪式 BE_726、召唤仪式 BE_085、教父卡扎库斯 CAP_405；
          * 两条仪式都在手：加留喉魔异教徒 BE_729；
          * 起手没有仪式：只留 1 张暮光龙卵 CATA_210；
          * 其余换掉；幸运币保留。
        """
        hand = list(getattr(state, "my_hand_cards", ()) or ())
        ids = [getattr(card, "card_id", None) for card in hand]
        has_cuthun = CARD_ID_CUTHUN_RITUAL in ids
        has_summon = CARD_ID_SUMMON_RITUAL in ids
        both_rituals = has_cuthun and has_summon
        kept_egg = False

        replace = []
        for index, card in enumerate(hand):
            card_id = getattr(card, "card_id", None)
            if not card_id:
                replace.append(index)
                continue
            if Dwys._is_coin(card_id):
                continue                                  # 幸运币：保留
            if card_id in (CARD_ID_CUTHUN_RITUAL, CARD_ID_SUMMON_RITUAL):
                continue                                  # 仪式必留
            if card_id == CARD_ID_KAZAKUS:
                continue                                  # 卡扎库斯必留（用户 2026-10-25）
            if both_rituals and card_id == CARD_ID_RITUALIST:
                continue                                  # 双仪式：留配合牌
            if (not has_cuthun and not has_summon
                    and card_id == CARD_ID_EGG and not kept_egg):
                kept_egg = True                           # 无仪式：留 1 张龙卵
                continue
            replace.append(index)

        names = [f"{getattr(hand[index], 'card_id', '?')}"
                 f"({card_name(getattr(hand[index], 'card_id', None)) or '?'})"
                 for index in replace]
        print(f"[动物园术留牌] 起手 {len(hand)} 张，换掉 {len(replace)} 张：{names}")
        return replace

    # ================================================================ 上下文
    @staticmethod
    def build_deck_context(state):
        """Search.search() 调用：从真实战局构建动物园术上下文。"""
        return DwysDeckContext.from_state(state)

    @staticmethod
    def _context(state) -> DwysDeckContext:
        context = getattr(state, "deck_context", None)
        if isinstance(context, DwysDeckContext):
            return context
        return DwysDeckContext()

    # ================================================================ 搜索辅助
    @staticmethod
    def card_text(card_id) -> str:
        return card_text(card_id)

    @staticmethod
    def card_name(card_id) -> str:
        return card_name(card_id)

    # ---------------------------------------------------------------- 目标
    # 本卡组需要目标的牌：漂浮教程 BE_094 点友方随从；克苏恩的凝视
    # BE_013t3（克苏恩仪式选项）点敌方随从；冰川裂片 CORE_UNG_205 / UNG_205
    # 点任意敌人（敌方随从或敌方英雄）。
    TARGETED_CARD_IDS = frozenset({
        CARD_ID_TUTORIAL, CARD_ID_CUTHUN_GAZE,
        CARD_ID_GLACIER, CARD_ID_GLACIER_LEGACY,
    })
    UNTARGETED_CARD_IDS = frozenset(
        card_id for card_id in CARD_ATTRIBUTES
        if card_id not in (CARD_ID_TUTORIAL, CARD_ID_CUTHUN_GAZE,
                           CARD_ID_GLACIER, CARD_ID_GLACIER_LEGACY))

    @staticmethod
    def requires_target(entity):
        """这张牌是否需要指定目标；认不出返回 None（交给通用文本推断）。"""
        card_id = getattr(entity, "card_id", None)
        if card_id in Dwys.TARGETED_CARD_IDS:
            return True
        if card_id in Dwys.UNTARGETED_CARD_IDS:
            return False
        return None

    @staticmethod
    def possible_targets(entity, state):
        """按 card_id 给出合法目标号位（1 基 AiSlot）；None = 交给通用推断。"""
        card_id = getattr(entity, "card_id", None)
        if card_id == CARD_ID_TUTORIAL:
            try:
                entries = ordered_board(state, "friendly")
            except Exception:
                return ()
            return tuple(
                AiSlot("board_slot", "friendly", position)
                for position, entry in enumerate(entries, 1)
                if entry.kind == "minion")
        if card_id == CARD_ID_CUTHUN_GAZE:      # 克苏恩的凝视：只能点敌方随从
            try:
                entries = ordered_board(state, "enemy")
            except Exception:
                return ()
            return tuple(
                AiSlot("board_slot", "enemy", position)
                for position, entry in enumerate(entries, 1)
                if entry.kind == "minion")
        if card_id in (CARD_ID_GLACIER, CARD_ID_GLACIER_LEGACY):
            # 冰川裂片：冻结一个敌人 = 敌方随从 + 敌方英雄都合法。
            try:
                entries = ordered_board(state, "enemy")
            except Exception:
                return (AiSlot("hero", "enemy", None),)
            slots = tuple(
                AiSlot("board_slot", "enemy", position)
                for position, entry in enumerate(entries, 1)
                if entry.kind == "minion")
            return slots + (AiSlot("hero", "enemy", None),)
        if card_id in Dwys.UNTARGETED_CARD_IDS:
            return ()
        return None

    @staticmethod
    def play_destination(state, card):
        """随从出场落点（1 基空隙号 AiSlot）；None = 执行层默认最右。

        用户规则：
        * 暗誓信徒 CATA_725：默认放最右，兆示召唤的古加尔士兵 CATA_725t
          也跟着站在最右，回合结束时右边没有随从可吃。
        * 不稳定的原型机 BE_207：例外，有暗誓信徒时仍放最右，故意喂给
          士兵（亡语给手牌随从 +1/+1，本身 0/1 被吃反而赚）。
        * 暮光龙卵 CATA_210：放士兵右边，回合结束被吃的是 0 攻龙卵，
          保护左边的 3/5 嘲讽（用户 2026-10-24 标注 t006-s0）。
        * 其余随从：有暗誓信徒/士兵时放它们左边，避免回合结束被士兵吃掉。
        """
        card_id = getattr(card, "card_id", None)
        if card_id in (CARD_ID_HERALD, CARD_ID_PROTOTYPE):
            return None
        try:
            entries = ordered_board(state, "friendly")
        except Exception:
            return None
        if card_id == CARD_ID_VAULT_GUARD:
            # 库区守卫：只放两侧都有随从的内侧空隙（buff 左右两只），
            # 优先贴大随从/能解场的随从（用户 2026-10-24）。
            gap, _score = Dwys._vault_guard_best_gap(state, entries)
            return AiSlot("board_slot", "friendly", gap) if gap else None
        positions = [
            position for position, entry in enumerate(entries, 1)
            if entry.kind == "minion"
            and getattr(entry.entity, "card_id", None)
            in (CARD_ID_HERALD, CARD_ID_SOLDIER)
        ]
        if not positions:
            return None
        if card_id == CARD_ID_EGG:
            # 士兵右边的空隙：士兵回合结束只会吃掉紧邻右侧的龙卵。
            return AiSlot("board_slot", "friendly", min(positions) + 1)
        # 插到最左边那只暗誓信徒/士兵的前面：空隙号 = 它的场面号位。
        return AiSlot("board_slot", "friendly", min(positions))

    # ================================================================ 场面评分
    @staticmethod
    def _hero_health_value(health: float) -> float:
        """英雄每一滴血的价值：越危险，每滴血越值钱（用户 2026-10-26）。

        <=10：×3；10~20：10+2×血量；>20：30+血量。分段在 10/20 处连续。
        """
        health = max(0, health or 0)
        if health <= 10:
            return health * 3.0
        if health <= 20:
            return 10.0 + 2.0 * health
        return 30.0 + health

    @staticmethod
    def _enemy_minion_count(state) -> int:
        """敌方随从数量（清空敌方场面的奖励按这个值结算）。"""
        return len(getattr(state, "oppo_minions", []) or [])

    @staticmethod
    def rankStaticState(state: AiState, items: list | None = None,
                        enemy_count_ref: int | None = None) -> float:
        """场面分：随从身材/关键字 + 双方英雄生命 + 撕裂风险。

        items 不是 None 时，把每一项加减分依次追加进去（标注台得分明细用）；
        追加顺序与累加顺序完全一致，因此 items 之和恒等于返回值。

        enemy_count_ref 是动作序列开始前敌方随从的数量：终局清空敌方场面时
        按它给分，由 rankState 传入；不传则用当前场面的敌方随从数。
        """
        res = 0.0
        # 末日预言者本回合杀不掉时，我方随从活不到下个回合开始：站场身材、
        # 幼龙/扎卡兹的站场补贴一律不计分，否则「随便出个随从再结束回合」
        # 会因为落场后的身材分反而成了最优（用户 2026-10-25 t006-s0）。
        doomed = Dwys._doomsayer_unkillable(state)
        if items is not None and doomed:
            Dwys._add_item(items, "末日预言者无法击杀：我方随从站场不计分",
                           "", 0.0)
        for index, minion in enumerate(
                getattr(state, "my_minions", []) or [], 1):
            if doomed:
                continue
            value = Dwys._minion_value(minion, mine=True)
            res += value
            if items is not None:
                Dwys._add_item(
                    items, f"我方随从 {index} 号位 {Dwys._card_label(minion)}",
                    Dwys._minion_detail(minion), value)
        threat = 0.0
        for index, minion in enumerate(
                getattr(state, "oppo_minions", []) or [], 1):
            # 凯洛斯的蛋未破壳时不打它反而是好事：留在对面慢慢破壳，
            # 打掉只会让它更快孵出 20/20（用户 2026-10-24）。
            if Dwys._is_unhatched_egg(minion):
                res += EGG_ALIVE_BONUS
                if items is not None:
                    Dwys._add_item(
                        items, f"敌方随从 {index} 号位 {Dwys._card_label(minion)}"
                               "（蛋未破壳）",
                        "留场奖励", EGG_ALIVE_BONUS)
            value = -Dwys._minion_value(minion, mine=False)
            res += value
            if items is not None:
                Dwys._add_item(
                    items, f"敌方随从 {index} 号位 {Dwys._card_label(minion)}",
                    Dwys._minion_detail(minion), value)
            # 下回合能出手的随从才计入威胁（冰冻/休眠/无法攻击不算）。
            if (getattr(minion, "frozen", 0) or getattr(minion, "dormant", 0)
                    or getattr(minion, "cant_attack", 0)):
                continue
            attack = getattr(minion, "attack", 0) or 0
            if attack < THREAT_HIGH_ATTACK:
                continue
            threat += attack * THREAT_HIGH_WEIGHT

        # 敌方空场：没有任何随从能反打/换掉我方场面。收益按对方初始场面的
        # 敌方随从数量结算，上限 5 分（用户 2026-10-29）。
        if not (getattr(state, "oppo_minions", []) or []):
            if enemy_count_ref is None:
                enemy_count_ref = Dwys._enemy_minion_count(state)
            bonus = min(ENEMY_EMPTY_BOARD_BONUS_CAP,
                        max(0, int(enemy_count_ref)))
            res += bonus
            if items is not None:
                Dwys._add_item(
                    items, "敌方空场",
                    f"初始敌方随从 {int(enemy_count_ref)} 个"
                    f"（上限 {ENEMY_EMPTY_BOARD_BONUS_CAP:g}）", bonus)

        my_hero = getattr(state, "my_hero", None)
        oppo_hero = getattr(state, "oppo_hero", None)
        my_health = max(0, getattr(my_hero, "health", 0) or 0)
        urgency = max(0.0, THREAT_URGENCY_HEALTH - my_health) \
            / THREAT_URGENCY_HEALTH
        threat_penalty = -threat * urgency
        res += threat_penalty
        if items is not None:
            Dwys._add_item(items, "敌方高威胁随从总量",
                           f"威胁值 {threat:g} × 紧迫度 {urgency:.2f}",
                           threat_penalty)
        my_hero_value = Dwys._hero_health_value(my_health) * MY_HERO_WEIGHT
        res += my_hero_value
        if items is not None:
            Dwys._add_item(
                items, f"我方英雄生命 {my_health:g}",
                f"生命价值 {Dwys._hero_health_value(my_health):g} × "
                f"权重 {MY_HERO_WEIGHT:g}", my_hero_value)
        oppo_health = getattr(oppo_hero, "health", 0) or 0
        oppo_hero_value = -Dwys._hero_health_value(oppo_health) \
            * ENEMY_HERO_WEIGHT
        res += oppo_hero_value
        if items is not None:
            Dwys._add_item(
                items, f"敌方英雄生命 {oppo_health:g}",
                f"生命价值 {Dwys._hero_health_value(oppo_health):g} × "
                f"权重 {ENEMY_HERO_WEIGHT:g}", oppo_hero_value)
        if oppo_hero is not None and oppo_health <= 0:
            # 斩杀：敌方英雄被打死，对局直接结束。血量项到 0 只是归零，
            # 胜负结果在静态分里看不到；不论伤害来自法术、攻击还是多张牌
            # 累计，统一按最终场面判定（用户 2026-10-28）。
            res += LETHAL_BONUS
            if items is not None:
                Dwys._add_item(items, "斩杀", "敌方英雄生命归零，对局结束",
                               LETHAL_BONUS)

        context = Dwys._context(state)
        tear_weight = (TEAR_LOW_HEALTH_PENALTY if my_health <= 15
                       else TEAR_PENALTY)
        tear_penalty = -context.tears_in_deck * tear_weight
        res += tear_penalty
        if items is not None:
            Dwys._add_item(items, f"撕裂牌 {context.tears_in_deck} 张",
                           f"{context.tears_in_deck} × {tear_weight:g}",
                           tear_penalty)
        return res

    @staticmethod
    def _add_item(items: list, label: str, detail: str, value: float) -> None:
        """追加一条得分明细（items 由标注台明细调用方传入）。"""
        items.append({"label": label, "detail": detail,
                      "value": float(value)})

    @staticmethod
    def _card_label(card) -> str:
        """卡牌标签：中文名（card_id），方便对照 card id 排查。"""
        card_id = (getattr(card, "card_id", None) if not isinstance(card, str)
                   else card)
        name = card_name(card_id) if card_id else ""
        if name:
            return f"{name}（{card_id}）"
        return card_id or "未知卡牌"

    @staticmethod
    def _minion_detail(minion) -> str:
        """随从身材分算式：攻击×权重 + 生命×权重 + 关键字附加。"""
        card_id = getattr(minion, "card_id", None)
        on_hand = str(getattr(minion, "zone", "") or "").upper() == "HAND"
        attack = getattr(minion, "attack", 0) or 0
        health = getattr(minion, "health", 0) or 0
        if on_hand and health <= 0:
            attack, health = item_stats(card_id)
        base = attack * ATTACK_WEIGHT + health * HEALTH_WEIGHT
        keyword = Dwys._minion_value(minion, mine=True) - base
        text = f"攻击 {attack:g}×{ATTACK_WEIGHT:g} + 生命 {health:g}×{HEALTH_WEIGHT:g}"
        if abs(keyword) > 1e-9:
            text += f" + 关键字 {keyword:g}"
        return text

    # ================================================================ 动作评分
    @staticmethod
    def rankActions(states: list[AiState], actions: list[AiAction],
                    items: list | None = None) -> float:
        """动作增量分：对序列里每个动作按它各自的前缀场面求增量并求和。

        最终序列得分 = 本函数 + rankStaticState(终局场面)，两部分严格不重复：
        随从身材、攻击换血/击杀/受伤等结果都已由终局静态分体现，这里只补
        搜索模拟看不到的卡牌效果、顺序与未来价值（用户 2026-10-26）。

        items 不是 None 时逐条记录动作分与顺序规则（标注台得分明细用），
        记录顺序与累加顺序一致，items 之和恒等于返回值。
        """
        if not actions:
            return 0.0
        gain = 0.0
        last_index = len(actions) - 1
        for index, action in enumerate(actions):
            state = states[index]
            is_last = index == last_index
            prefix = actions[:index + 1]          # 按当时的位置判断先后
            if action.action == ActionKind.PLAY_CARD and Dwys._is_coin(
                    getattr(action, "card_id", None)):
                # 硬币只在“确实凑出一张值得跳币的牌、并且那张牌真的打出去了”
                # 时才值分（见 _coin_gain）。传 actions[index + 1:]：usage 判断
                # 需要看到序列最后那个还没模拟的动作，而法力还原由 _coin_gain
                # 自己剔除它。
                delta = Dwys._coin_gain(state, action, actions[index + 1:])
            elif action.action == ActionKind.PLAY_CARD:
                # 法术 / 随从都按“打出那一刻”的场面算；随从的身材不再计入，
                # 只叠加卡牌效果与未来价值。
                delta = Dwys._play_gain(
                    state, action, prefix, later=actions[index + 1:])
                Dwys._log_action(state, action, delta, actions)
            elif action.action == ActionKind.ATTACK:
                delta = Dwys._attack_gain(state, action)
            elif action.action == ActionKind.CHOOSE_DISCOVER:
                delta = Dwys._discover_gain(state, action)
                Dwys._log_action(state, action, delta, actions)
            elif action.action == ActionKind.USE_HERO_POWER:
                delta = Dwys._hero_power_gain(state, action, prefix)
            elif action.action == ActionKind.USE_MINION_ABILITY:
                # 本体激活：搜索没能模拟技能效果的全部增量，逐次补分。
                delta = Dwys._minion_ability_gain(state, action, prefix)
                Dwys._log_action(state, action, delta, actions)
            elif action.action == ActionKind.END_TURN:
                delta = Dwys._cuthun_end_turn_penalty(state)
            else:
                delta = 0.0
            gain += delta
            if items is not None:
                Dwys._add_item(
                    items, Dwys._action_label(action, index),
                    getattr(action, "instruction", "") or "", delta)
            if action.action == ActionKind.ATTACK and not is_last:
                # 突袭解嘲讽是“为其余随从开路”：只有后面还有动作时才有这份
                # 序列价值。
                bonus = Dwys._rush_clear_taunt_bonus(state, action)
                gain += bonus
                if items is not None:
                    Dwys._add_item(
                        items, f"{index + 1}. 突袭解嘲讽开路奖励", "", bonus)
            rightmost = Dwys._rightmost_copy_penalty(state, action)
            gain += rightmost
            if items is not None and rightmost:
                Dwys._add_item(
                    items, f"{index + 1}. 同名手牌优先出右边", "", rightmost)
        # 序列级顺序规则仍按“最后一个动作之前”的场面判断：这些函数会把
        # 序列里已花的费用加回去还原开局信息（英雄技能加分例外：直接看
        # 序列开始时的场面，见 _hero_power_draw_bonus）。
        last_state = states[-1]
        rules = (
            ("顺序规则：漂浮教程早于喉魔",
             Dwys._tutorial_before_ritualist_penalty(last_state, actions)),
            ("顺序规则：攻击后不再出牌",
             Dwys._attack_order_penalty(actions)),
            ("顺序规则：关键牌出牌顺序",
             Dwys._key_card_order_penalty(actions)),
            ("顺序规则：咒怨之墓优先",
             Dwys._crypt_first_penalty(last_state, actions)),
            ("顺序规则：英雄技能加分",
             Dwys._hero_power_draw_bonus(states, actions)),
            ("顺序规则：熵能裂隙斩杀",
             Dwys._entropy_lethal_bonus(last_state, actions)),
            ("动作：栉龙抽到的牌当回合打出",
             Dwys._pterrordax_drawn_play_bonus(last_state, actions)),
            ("顺序规则：打脸伤害",
             Dwys._face_damage_bonus(last_state, actions)),
        )
        for label, value in rules:
            gain += value
            if items is not None:
                Dwys._add_item(items, label, "", value)
        return gain

    @staticmethod
    def _action_label(action: AiAction, index: int) -> str:
        """动作分行的标题（序号 + 动作中文描述）。"""
        prefix = f"{index + 1}. "
        card_id = getattr(action, "card_id", None)
        action_kind = action.action
        if action_kind == ActionKind.PLAY_CARD:
            if Dwys._is_coin(card_id):
                return prefix + f"使用硬币 {Dwys._card_label(card_id)}"
            return prefix + "打出 " + Dwys._card_label(card_id)
        if action_kind == ActionKind.ATTACK:
            return prefix + "攻击 " + Dwys._card_label(card_id)
        if action_kind == ActionKind.CHOOSE_DISCOVER:
            choice = getattr(action, "choice_name", None) \
                or getattr(action, "choice_card_id", None)
            return prefix + "发现选择 " + Dwys._card_label(choice)
        if action_kind == ActionKind.USE_HERO_POWER:
            return prefix + "使用英雄技能"
        if action_kind == ActionKind.USE_MINION_ABILITY:
            return prefix + "激活随从技能 " + Dwys._card_label(card_id)
        if action_kind == ActionKind.END_TURN:
            return prefix + "结束回合"
        return prefix + str(getattr(action_kind, "value", action_kind))

    @staticmethod
    def _attack_order_penalty(actions: list[AiAction]) -> float:
        """攻击之后还有出牌动作：序列扣分（先把法力转成场面，攻击最后）。

        只在评分里扣分，候选生成不动（用户要求的“候选与评分解耦”）。
        """
        played_after_attack = False
        for action in reversed(actions):
            if action.action == ActionKind.PLAY_CARD:
                played_after_attack = True
            elif (action.action == ActionKind.ATTACK
                  and played_after_attack):
                return ATTACK_BEFORE_PLAY_PENALTY
        return 0.0

    @staticmethod
    def _tutorial_before_ritualist_penalty(state: AiState,
                                           actions: list[AiAction]) -> float:
        """喉魔还在手里时先打漂浮教程：每张扣 TUTORIAL_AURA_EXTRA。

        漂浮教程的加成按本方牌“法力值消耗的 2 倍”结算，喉魔的光环把它抬高
        1 费就等于每张多 +2/+2（用户 2026-10-25 标注 t010-s0：先上喉魔，
        让教程从 2 费变 3 费再贴）。判断口径是“本回合一开始就凑得出喉魔 +
        加费后的教程”——凑得出时先把法力花在别处再贴教程，等于白丢这一档，
        每张扣 TUTORIAL_AURA_EXTRA（正好是光环能多换来的 12 分）。
        凑不出来时先打教程不算错，不扣。
        """
        if not actions or not Dwys._hand_has(state, CARD_ID_RITUALIST):
            return 0.0
        ritualist_cost = min(
            (Dwys._card_cost(card)
             for card in getattr(state, "my_hand_cards", []) or []
             if getattr(card, "card_id", None) == CARD_ID_RITUALIST),
            default=None)
        if ritualist_cost is None:
            return 0.0
        # 还原序列开始时的法力：state 是“最后一个动作之前”的场面。
        start_mana = max(0, getattr(state, "my_last_mana", 0) or 0)
        for action in actions:
            if action.action in (ActionKind.PLAY_CARD,
                                 ActionKind.USE_HERO_POWER):
                start_mana += max(0, getattr(action, "cost", 0) or 0)
        penalty = 0.0
        played_ritualist = False
        for action in actions:
            if action.action != ActionKind.PLAY_CARD:
                continue
            card_id = getattr(action, "card_id", None)
            if card_id == CARD_ID_RITUALIST:
                played_ritualist = True
                continue
            if (card_id != CARD_ID_TUTORIAL or played_ritualist
                    or Dwys._is_coin(card_id)):
                continue
            cost = max(0, getattr(action, "cost", 0) or 0)
            if start_mana >= ritualist_cost + cost + 1:
                penalty -= TUTORIAL_AURA_EXTRA
        return penalty

    @staticmethod
    def _key_card_order_penalty(actions: list[AiAction]) -> float:
        """关键牌（卡扎库斯/库区守卫/栉龙）后置出场：整条序列扣分。

        用户 2026-10-24 确认的方案：不改候选生成，只在评分里检查顺序——
        关键牌排在别的出牌动作之后时扣 KEY_CARD_LATE_PENALTY。
        硬币只是凑费手段，放在关键牌前面不算抢占时机。

        栉龙额外严格一档：攻击虽然不花法力，但也会先用掉本回合的时机——
        它的战吼抽到的牌要当回合用掉，先把攻击打完再抽牌等于少看一张牌
        （用户 2026-10-24 标注 t008-s0：4 费回合应先出栉龙再攻击）。
        栉龙排在别的出牌之后不再扣分：真正的约束是剩余法力（低法力惩罚
        见 `_minion_gain`），硬性要求“栉龙必须第一个出”会把“先清场再补栉龙”
        压死（t009-s0）。
        """
        played_before = False
        attacked_before = False
        penalty = 0.0
        for action in actions:
            if action.action == ActionKind.ATTACK:
                attacked_before = True
                continue
            if action.action != ActionKind.PLAY_CARD:
                continue
            card_id = getattr(action, "card_id", None)
            if card_id == CARD_ID_PTERRORDAX:             # 栉龙
                if attacked_before:
                    penalty += KEY_CARD_LATE_PENALTY
            elif card_id in KEY_FIRST_CARD_IDS and played_before:
                penalty += KEY_CARD_LATE_PENALTY
            if not Dwys._is_coin(card_id):
                played_before = True
        return penalty

    @staticmethod
    def _state_at_sequence_start(state: AiState, actions: list[AiAction]):
        """还原序列开始那一刻的 (手牌, 剩余法力, 场面格数)。

        评分拿到的 state 是序列末尾的场面：打出的牌已经离手、法力已经花掉、
        随从已经落场。要把序列里的出牌与英雄技能的费用加回来，才能判断
        “起始手里有没有值得先打的牌”（用户 2026-10-25 确认的守卫条件）。
        """
        remaining = max(0, getattr(state, "my_last_mana", 0) or 0)
        board_count = getattr(state, "my_board_slot_num", 0) or 0
        cards = list(getattr(state, "my_hand_cards", []) or [])
        for action in actions or ():
            if action.action in (ActionKind.PLAY_CARD,
                                 ActionKind.USE_HERO_POWER):
                remaining += max(0, getattr(action, "cost", 0) or 0)
            if action.action != ActionKind.PLAY_CARD:
                continue
            if action.card_type == "MINION":
                board_count -= 1
            card = getattr(action, "card", None)
            if card is not None:
                cards.append(card)
        return cards, remaining, board_count

    @staticmethod
    def _pterrordax_drawn_card(state: AiState, actions=()):
        """带“易丢弃”标记的手牌：栉龙本回合战吼抽到、还没打出的那张。

        栉龙 TLC_603「战吼：抽一张牌。亡语：弃掉该牌」——抽到的牌进手牌最
        右侧。state 没有“抽牌来源”字段，只能按两条线索识别：
          * 栉龙是本回合才落场的（num_turns_in_play == 0）；
          * 序列开始时手牌最右侧那张（新抽的牌总是追加在最右边）。
        栉龙本身是本序列才打出的情况直接放弃判断：抽牌没有被模拟，手牌里
        看不出新牌，硬按最右侧算会张冠李戴。
        """
        for action in actions or ():
            if (action.action == ActionKind.PLAY_CARD
                    and getattr(action, "card_id", None) == CARD_ID_PTERRORDAX):
                return None
        if not any(
                getattr(minion, "card_id", None) == CARD_ID_PTERRORDAX
                and not getattr(minion, "num_turns_in_play", 1)
                for minion in getattr(state, "my_minions", []) or ()):
            return None
        cards, _remaining, _board = Dwys._state_at_sequence_start(
            state, actions)
        if not cards:
            return None
        return max(cards, key=lambda card: getattr(card, "zone_pos", 0) or 0)

    @staticmethod
    def _pterrordax_drawn_play_bonus(state: AiState, actions) -> float:
        """栉龙抽到的那张牌本回合打出去了：救下这张牌，+20（用户 2026-10-28）。

        原来这里是“留在手里 -25”的扣分；改成只在真打出去时加分，留在手里
        不再扣分。留在手里的价值低由“这张牌押在栉龙身上”自然体现，不再额外惩罚。
        """
        marked = Dwys._pterrordax_drawn_card(state, actions)
        if marked is None:
            return 0.0
        for action in actions or ():
            if (action.action == ActionKind.PLAY_CARD
                    and Dwys._same_card(getattr(action, "card", None), marked)):
                return PTERRORDAX_DRAWN_PLAY_BONUS
        return 0.0

    @staticmethod
    def _has_strong_card_at_start(state: AiState, actions: list[AiAction]) -> bool:
        """序列开始时手里是否有 >=3 费、本回合付得起且出得去的牌。

        临近斩杀的打脸 / 抽牌规则用它做守卫：手里还有像样的牌可打时，不该
        为了博斩杀先抽牌或跳过出牌（用户 2026-10-25 确认，避免误伤盒子AI
        打出快速庭审 CAP_405tb1 的序列）。
        """
        cards, remaining, board_count = Dwys._state_at_sequence_start(
            state, actions)
        for card in cards:
            card_id = getattr(card, "card_id", None)
            if card_id in (CARD_ID_CRYPT, CARD_ID_RITUALIST):
                continue
            if Dwys._card_cost(card) < COIN_ENABLED_MIN_COST:
                continue
            if Dwys._is_playable_now(state, card, remaining, board_count):
                return True
        return False

    @staticmethod
    def _harmless_enemy_board(state: AiState) -> bool:
        """敌方没有能形成威胁的随从：无嘲讽，且攻击力都 <= 3。"""
        enemies = getattr(state, "oppo_minions", []) or []
        if any(getattr(m, "taunt", 0) for m in enemies):
            return False
        return not any((getattr(m, "attack", 0) or 0) > HARMLESS_ATTACK_MAX
                       for m in enemies)

    @staticmethod
    def _should_race_face(state: AiState, actions: list[AiAction]) -> bool:
        """临近斩杀：敌方英雄血量 1-15、敌方无威胁、手里没有 >=3 费可出牌。

        “优先打脸”的统一口径（用户 2026-10-25 确认 t015-s4：敌人血不多了，
        先打脸再出牌）：满足时每次攻击敌方英雄补 FACE_DAMAGE_BONUS。
        """
        if not actions or not Dwys._harmless_enemy_board(state):
            return False
        if Dwys._has_strong_card_at_start(state, actions):
            return False
        enemy_health = getattr(state.oppo_hero, "health", 0) or 0
        return 0 < enemy_health <= FACE_DAMAGE_MAX_HEALTH

    @staticmethod
    def _ritualist_tutorial_combo(state: AiState) -> bool:
        """当前能否打出「漂浮教程按更高一档费用」的强力组合。

        判定条件（用户 2026-10-25 标注 t010-s0）：
          * 两张牌都在手里：喉魔费用 + 教程费用 + 光环的 1 费 <= 本回合法力；
          * 或喉魔已经站在场上（光环生效）：教程按「费用 + 1」判断付得起。
        成立时手里就有强力组合可打，不必为了发现先出咒怨之墓。
        """
        remaining = max(0, getattr(state, "my_last_mana", 0) or 0)
        costs = {}
        for card in getattr(state, "my_hand_cards", []) or []:
            card_id = getattr(card, "card_id", None)
            if card_id not in (CARD_ID_RITUALIST, CARD_ID_TUTORIAL):
                continue
            cost = Dwys._card_cost(card)
            costs[card_id] = min(costs.get(card_id, cost), cost)
        tutorial_cost = costs.get(CARD_ID_TUTORIAL)
        if tutorial_cost is None:
            return False
        if CARD_ID_RITUALIST in costs:
            return (costs[CARD_ID_RITUALIST] + tutorial_cost + 1
                    <= remaining)
        # 喉魔已在场上：光环已经让教程加 1 费，直接看加费后还付不付得起。
        if any(getattr(minion, "card_id", None) == CARD_ID_RITUALIST
               for minion in getattr(state, "my_minions", []) or []):
            return tutorial_cost + 1 <= remaining
        return False

    @staticmethod
    def _crypt_first_penalty(state: AiState, actions: list[AiAction]) -> float:
        """咒怨之墓 TLC_451 第一个出（用户 2026-10-25 确认 t011-s0）。

        手里没有别的 >=3 费可出牌、且本回合有 3 点以上法力时，先出咒怨之墓
        才能留够费用做出最佳发现；首个动作不是它就扣 CRYPT_FIRST_PENALTY。
        """
        if not actions:
            return 0.0
        first = actions[0]
        if (first.action == ActionKind.PLAY_CARD
                and getattr(first, "card_id", None) == CARD_ID_CRYPT):
            # 咒怨之墓先出，但手里有一张吃掉几乎全部法力的大牌：发现到的
            # 临时牌这回合打不出去，等于白扔一张咒怨之墓（用户 2026-10-25
            # 确认 152250 t014-s0：7 费快速庭审在手里时先打庭审，不要先用
            # 0 费咒怨白赚一次发现）。
            return (CRYPT_LOW_MANA_PENALTY
                    if Dwys._crypt_first_wastes_discover(state, actions)
                    else 0.0)
        if not Dwys._hand_has(state, CARD_ID_CRYPT):
            return 0.0
        if (getattr(state, "my_last_mana", 0) or 0) < CRYPT_FIRST_MIN_MANA:
            return 0.0
        if Dwys._has_strong_playable_card(state):
            return 0.0
        if Dwys._ritualist_tutorial_combo(state):
            # 喉魔 + 教程能凑出 3 费教程，本身就够“强力可出”，
            # 不该为了发现把它拆掉（t010-s0）。
            return 0.0
        return CRYPT_FIRST_PENALTY

    @staticmethod
    def _crypt_first_wastes_discover(state: AiState,
                                     actions: list[AiAction]) -> bool:
        """先出咒怨之墓后，本回合是否还有“吃掉几乎全部法力”的大牌要打。

        咒怨之墓发现的是临时牌，回合结束没用掉就消失。序列里还有一张
        >=3 费、打得出的牌，且打出它之后只剩 <= CRYPT_LOW_MANA_MAX 费时，
        发现的临时牌必然付不起——这次发现等于白扔（与 CRYPT_LOW_MANA_PENALTY
        同一条思路：7 费快速庭审在手里时先打庭审，别先出 0 费咒怨）。
        """
        first = actions[0]
        # 咒怨之墓自己 0 费：这一步之后可用的法力
        mana = Dwys._mana_at_play(state, first, actions[1:])
        board_count = getattr(state, "my_board_slot_num", 0) or 0
        cards = list(getattr(state, "my_hand_cards", []) or ())
        for action in actions[1:]:
            if action.action != ActionKind.PLAY_CARD:
                continue
            card = getattr(action, "card", None)
            if card is not None:
                cards.append(card)
        for card in cards:
            card_id = getattr(card, "card_id", None)
            if card_id in (CARD_ID_CRYPT, CARD_ID_RITUALIST):
                continue
            cost = Dwys._card_cost(card)
            if cost < COIN_ENABLED_MIN_COST:
                continue
            if not Dwys._is_playable_now(state, card, mana, board_count):
                continue
            if cost >= mana - CRYPT_LOW_MANA_MAX:
                return True
        return False

    @staticmethod
    def _hero_power_draw_bonus(states: list[AiState],
                               actions: list[AiAction]) -> float:
        """英雄技能（生命分流）的全部加分都在这一个地方算。

        用户 2026-10-29：原来逐动作的 _hero_power_gain 也给一份基础分，同一个
        分流被记成 +2 和 +9 两部分；现在只有这里给分，且同一个分流只按
        “最强的一个理由”加一次（满场、抽牌等理由不叠加）。

        判断一律用序列开始时的场面（states[0]）：同一个分流排在第一位还是
        第四位，抽牌价值都一样，只有“本回合第一个动作”多 +1。

        * 抽牌价值：见 _hero_power_draw_value；
        * 本回合第一个动作用技能：+HERO_POWER_FIRST_ACTION_BONUS（抽到的牌
          可能改变后续选择），非第一手不加不扣。
        """
        total = 0.0
        start = states[0]
        value = Dwys._hero_power_draw_value(start)
        first = not getattr(start, "acted_this_turn", False)
        for index, action in enumerate(actions or ()):
            if action.action != ActionKind.USE_HERO_POWER:
                continue
            total += value
            if index == 0 and first:
                total += HERO_POWER_FIRST_ACTION_BONUS
        return total

    @staticmethod
    def _hero_power_draw_value(state: AiState) -> float:
        """分流抽牌在开局场面下值多少分：取最强的一个理由，不叠加。

        用户 2026-10-29：分流只按一处加分，同一动作不能既算“满场抽牌”又算
        “博斩杀抽牌”（t014-s1 里同一分流被加了两遍）。

        理由（都不看分流排第几位）：
          * 博斩杀：本回合场攻打完，敌方还剩 1~15 血且还打不死——抽 buff/直伤
            补刀（HERO_POWER_DRAW_BONUS）；
          * 超级威胁：敌方有 10+ 攻随从、手里没有污亵——抽牌找解；
          * 满场：7 格铺满——抽牌找群体增益；
          * 空场且手里没有随从可铺——抽牌找铺场资源。
        """
        enemy_health = getattr(state.oppo_hero, "health", 0) or 0
        my_health = getattr(state.my_hero, "health", 30) or 0
        attack = sum(max(0, getattr(m, "attack", 0) or 0)
                     for m in getattr(state, "my_minions", []) or []
                     if not getattr(m, "exhausted", 0))
        enemy_after = enemy_health - attack
        enemy_damage = Dwys._enemy_face_damage_next_turn(state)
        safe = my_health > 8 and my_health - 2 - enemy_damage > 3

        value = 0.0
        # 博斩杀：打完还剩血、但已经进入补刀射程。已经能斩杀时先打脸/解场，
        # 不该花 2 费 2 血去抽牌（attack >= enemy_health 时 enemy_after <= 0）。
        if (0 < enemy_after <= HERO_POWER_DRAW_MAX_HEALTH
                and my_health >= HERO_POWER_DRAW_MIN_MY_HEALTH
                and not Dwys._has_strong_card_at_start(state, ())
                and not Dwys._pterrordax_covers_draw(state, ())):
            value = max(value, HERO_POWER_DRAW_BONUS)
        # 超级威胁：敌方单只 10+ 攻、手里没污亵 -> 抽牌找答案。
        threat = max(
            (max(0, getattr(minion, "attack", 0) or 0)
             for minion in getattr(state, "oppo_minions", []) or []),
            default=0)
        has_defile = any(
            getattr(card, "card_id", None) == CARD_ID_DEFILE
            for card in getattr(state, "my_hand_cards", []) or [])
        if safe and threat >= HERO_POWER_THREAT_ATTACK and not has_defile:
            value = max(value, HERO_POWER_THREAT_BONUS)
        # 满场：随从铺不下，剩余法力只够抽牌找群体增益。
        if (getattr(state, "my_board_slot_num", 0) or 0) >= 7:
            value = max(value, HERO_POWER_FULL_BOARD_BONUS)
        # 空场且手里没有随从可铺：抽牌找铺场资源。
        if (safe and (getattr(state, "my_minion_num", 0) or 0) == 0
                and Dwys._hand_minion_count(state)
                <= HERO_POWER_EMPTY_BOARD_HAND_MINIONS):
            value = max(value, HERO_POWER_EMPTY_BOARD_BONUS)
        return value

    @staticmethod
    def _pterrordax_covers_draw(state: AiState, actions) -> bool:
        """栉龙本身就是抽牌，本回合不需要再靠分流过牌。

        两种情况都算“过牌已有着落”：
          * 手里还捏着一张打得出的栉龙——免费的抽牌没用就先花 2 费 2 血去
            分流是浪费（2026-10-24 标注 202733 t008-s0：4 费回合人类先出
            栉龙，AI 却把分流的过牌补贴算成最优）；
          * 序列里已经打出过栉龙——过牌只值一次，不能又给分流补一份。
        """
        for action in actions or ():
            if (action.action == ActionKind.PLAY_CARD
                    and getattr(action, "card_id", None) == CARD_ID_PTERRORDAX):
                return True
        cards, remaining, board_count = Dwys._state_at_sequence_start(
            state, actions)
        for card in cards:
            if getattr(card, "card_id", None) != CARD_ID_PTERRORDAX:
                continue
            if Dwys._is_playable_now(state, card, remaining, board_count):
                return True
        return False

    @staticmethod
    def _entropy_lethal_bonus(state: AiState, actions: list[AiAction]) -> float:
        """续连熵能 TIME_026 斩杀（用户 2026-10-25 确认 t019-s2）。

        发现界面最后选择了熵能（全场随从 +1/+1）、敌方无嘲讽、且加完后场攻
        总和足以斩杀时，直接判为最优选项。选择菜单按号位回查
        general_choice_cards：号位与字典键可能是 0 基或 1 基，四种都试。
        """
        if not actions:
            return 0.0
        last = actions[-1]
        if last.action != ActionKind.CHOOSE_DISCOVER:
            return 0.0
        options = getattr(state, "general_choice_cards", None) or {}
        index = getattr(last.source, "index", None)
        card_id = None
        for key in (index - 1, index, str(index - 1), str(index)):
            if options.get(key):
                card_id = str(options[key])
                break
        if card_id != CARD_ID_ENTROPY:
            return 0.0
        if any(getattr(m, "taunt", 0)
               for m in getattr(state, "oppo_minions", []) or []):
            return 0.0
        hero_health = getattr(state.oppo_hero, "health", 0) or 0
        attack = sum(max(0, getattr(m, "attack", 0) or 0) + 1
                     for m in getattr(state, "my_minions", []) or [])
        return ENTROPY_LETHAL_BONUS if attack >= hero_health else 0.0

    @staticmethod
    def _face_damage_bonus(state: AiState, actions: list[AiAction]) -> float:
        """临近斩杀、敌方无威胁时攻击敌方英雄补分。

        用户 2026-10-25 确认 t015-s4：敌方英雄血量 1-15、敌方随从都不构成
        威胁、且序列开始时手里没有 >=3 费可出的牌时，每次攻击敌方英雄
        （打脸）加 FACE_DAMAGE_BONUS。
        """
        if not actions or not Dwys._should_race_face(state, actions):
            return 0.0
        return sum(FACE_DAMAGE_BONUS for a in actions
                   if a.action == ActionKind.ATTACK and a.target is not None
                   and a.target.kind == "hero" and a.target.owner == "enemy")

    @staticmethod
    def _rush_clear_taunt_bonus(state: AiState, action: AiAction) -> float:
        """突袭随从攻击敌方嘲讽随从：补分（用户 2026-10-25 确认 t009-s1）。

        只在“突袭的攻击力刚好用得上”时加分：嘲讽只剩 1 血时，1 攻随从就能
        解掉，把 5 攻突袭浪费在上面属于溢出（205200 t009-s2 人类用 1 攻栉龙
        解 6/1 嘲讽，突袭留着打别处）。攻击力 <= 目标生命才认为突袭这一撞
        是必要的。
        """
        attacker = getattr(action, "card", None)
        if attacker is None or not getattr(attacker, "rush", 0):
            return 0.0
        if action.target is None or action.target.kind != "board_slot":
            return 0.0
        defender = Dwys._board_minion(state, "enemy", action.target.index)
        if defender is None or not getattr(defender, "taunt", 0):
            return 0.0
        attack = max(0, getattr(attacker, "attack", 0) or 0)
        health = max(0, getattr(defender, "health", 0) or 0)
        if health <= 0 or attack > health:
            return 0.0
        return RUSH_CLEAR_TAUNT_BONUS

    @staticmethod
    def _rightmost_copy_penalty(state: AiState, action: AiAction) -> float:
        """同名手牌优先出右边那张（用户 2026-10-23 规则）。

        新抽到的牌总是追加在手牌最右边；栉龙的亡语弃掉的正是它抽到的那张。
        两手同名牌价值一样时，先出右边那张能把“可能被弃掉”的牌先用掉。
        只在右侧存在同 card_id、同费用的副本时扣 0.5 分——只破同分，
        不改变任何实质收益比较。
        """
        if action.action != ActionKind.PLAY_CARD:
            return 0.0
        card_id = getattr(action, "card_id", None)
        source = getattr(action, "source", None)
        if not card_id or getattr(source, "kind", None) != "hand":
            return 0.0
        position = getattr(source, "index", 0) or 0
        hand = list(getattr(state, "my_hand_cards", []) or [])
        if not 1 <= position <= len(hand):
            return 0.0
        if getattr(hand[position - 1], "card_id", None) != card_id:
            return 0.0              # 累计序列里的旧动作：号位已对不上，不判断
        cost = int(getattr(action, "cost", 0) or 0)
        for other in hand[position:]:
            if getattr(other, "card_id", None) != card_id:
                continue
            if Dwys._card_cost(other) != cost:
                continue            # 右边那张更贵，不算“情况差不多”
            return -SAME_CARD_RIGHT_PENALTY
        return 0.0

    @staticmethod
    def rankState(state: AiState, actions: list[AiAction]) -> float:
        """关键函数：终局场面分 + 全序列动作增量（用户 2026-10-26）。

        state 是动作序列开始前的场面；这里按顺序模拟每个动作，记录各自的
        前缀场面，最后对“做完整个序列”的新场面算静态分。不再使用
        “最后一个动作之前的场面”，也不再漏掉路径上早前动作的收益。
        """
        if not actions:
            return Dwys.rankStaticState(state)
        if not isinstance(state, AiState):
            # 单元测试可能直接传 StrategyState：先转成搜索用的精简场面。
            state = AiState.from_strategy_state(state)
        # 延迟 import，避免 ai 包在模块加载阶段反向依赖 search 的搜索流程。
        # 只用通用的 doAction 模拟器，不引入任何具体卡牌知识。
        from search.search import doAction

        prefix_states: list[AiState] = []
        current = state
        for action in actions:
            prefix_states.append(current)
            current = doAction(current, action)
        # 清空敌方场面的奖励按序列开始前的敌方随从数结算（用户 2026-10-29）。
        enemy_count_ref = Dwys._enemy_minion_count(state)
        return (Dwys.rankStaticState(current,
                                     enemy_count_ref=enemy_count_ref)
                + Dwys.rankActions(prefix_states, actions))

    @staticmethod
    def rankStateDetailed(state: AiState, actions: list[AiAction]):
        """带明细的 rankState：返回 (总分, breakdown)，总分与 rankState 一致。

        只供标注台得分面板使用；搜索热路径仍走 rankState（不构建明细）。
        """
        if not actions:
            if not isinstance(state, AiState):
                state = AiState.from_strategy_state(state)
            static_items: list[dict] = []
            static_total = Dwys.rankStaticState(state, static_items)
            return static_total, Dwys._breakdown(static_items, [], static_total,
                                                 0.0)
        if not isinstance(state, AiState):
            # 单元测试可能直接传 StrategyState：先转成搜索用的精简场面。
            state = AiState.from_strategy_state(state)
        from search.search import doAction

        prefix_states: list[AiState] = []
        current = state
        for action in actions:
            prefix_states.append(current)
            current = doAction(current, action)
        # 清空敌方场面的奖励按序列开始前的敌方随从数结算（用户 2026-10-29）。
        enemy_count_ref = Dwys._enemy_minion_count(state)
        static_items: list[dict] = []
        static_total = Dwys.rankStaticState(
            current, static_items, enemy_count_ref=enemy_count_ref)
        action_items: list[dict] = []
        action_total = Dwys.rankActions(prefix_states, actions, action_items)
        return (static_total + action_total,
                Dwys._breakdown(static_items, action_items,
                                static_total, action_total))

    @staticmethod
    def _breakdown(static_items: list, action_items: list,
                   static_total: float, action_total: float) -> dict:
        """组装标注台得分明细结构。"""
        return {
            "supported": True,
            "total": float(static_total + action_total),
            "static_total": float(static_total),
            "action_total": float(action_total),
            "static_items": list(static_items),
            "action_items": list(action_items),
        }

    @staticmethod
    def _log_action(state: AiState, action: AiAction, gain: float, actions) -> None:
        """只记录本回合第一个动作，避免搜索树里刷屏（复盘用）。

        选择菜单的 AiAction 没有 card_id，必须用号位回查 general_choice_cards；
        否则会拿 "choice#1" 去查 cards.json，触发一次无谓的卡表重下。
        """
        if len(actions) != 1:
            return
        if action.action == ActionKind.CHOOSE_DISCOVER:
            card_id = Dwys._choice_card_id(state, action)
            label = f"选择{getattr(action.source, 'index', '?')}号位"
            print(f"[动物园术] {label} {card_id or '?'}"
                  f"（{card_name(card_id) or '?'}）{gain:+.1f}")
            return
        card_id = getattr(action, "card_id", None)
        print(f"[动物园术] {card_id or '?'}"
              f"（{card_name(card_id) or '?'}）{gain:+.1f}")

    # ---------------------------------------------------------------- 出牌
    @staticmethod
    def _play_gain(state: AiState, action: AiAction,
                   actions: list[AiAction],
                   later: list[AiAction] = ()) -> float:
        card_id = getattr(action, "card_id", None)
        if Dwys._is_coin(card_id):
            return Dwys._coin_gain(state, action, later)
        if action.card_type == "SPELL":
            gain = Dwys._spell_gain(state, action, actions, later)
        elif action.card_type == "MINION":
            gain = Dwys._minion_gain(state, action, actions)
        else:
            return 0.0
        if card_id in (CARD_ID_CRYPT, CARD_ID_PTERRORDAX):
            # 咒怨之墓/栉龙抽到（发现）的那张牌在模拟里看不见（不会进手牌），
            # 按“当回合用掉一次操作”补分；但只有剩余法力够把它打出去
            # （≥ ai.card_rules.drawn_card_mana_reserve 预留的 3 费）才给，
            # 否则这 +10 是凭空加分（用户 2026-10-30）。
            mana_left = max(0, (getattr(state, "my_last_mana", 0) or 0)
                            - max(0, getattr(action, "cost", 0) or 0))
            if mana_left >= DRAWN_CARD_PLAY_RESERVE_MANA:
                gain += (CRYPT_DRAWN_CARD_PLAY_BONUS
                         if card_id == CARD_ID_CRYPT
                         else DRAWN_CARD_PLAY_BONUS)
        if card_id == CARD_ID_PTERRORDAX:
            # 出栉龙后只剩 ≤2 费：抽到的牌当回合打不出去，等于把牌押在栉龙
            # 身上（用户 2026-10-28：与咒怨之墓统一 -12）。
            if mana_left <= CRYPT_LOW_MANA_MAX:
                gain += PTERRORDAX_LOW_MANA_PENALTY
        if Dwys._is_crypt_temporary_card(getattr(action, "card", None)):
            # 咒怨之墓刚发现的临时牌：本回合打出去才留得住（用户 2026-10-28）。
            gain += CRYPT_TEMP_PLAY_BONUS
        return gain

    @staticmethod
    def _is_crypt_temporary_card(card) -> bool:
        """这张手牌是不是咒怨之墓（TLC_451）刚发现、本回合不打就丢的临时牌。

        日志实测没有 TEMPORARY tag（012753 整局 0 次），真实对局只能靠来源牌
        识别：发现的牌带 LAST_AFFECTED_BY = 咒怨之墓的实体（t006 手牌 entity 7
        的 LAST_AFFECTED_BY=13，entity 13 是 TLC_451），由 log_state 解析成
        created_by。标注用例里的旧快照仍带 temporary 标记，两种信号都认。
        """
        if card is None:
            return False
        if getattr(card, "temporary", 0):
            return True
        return str(getattr(card, "created_by", "") or "") == CARD_ID_CRYPT

    @staticmethod
    def _coin_gain(state: AiState, action=None, later=()) -> float:
        """幸运币：只值它“真正凑出来并打出去”的那张牌（用户 2026-10-24/25）。

        搜索已经模拟硬币的 +1 法力（search.doAction 会把已用法力回退 1 点），
        所以这里只看硬币之后**真的打出了**哪张牌：先还原打出硬币那一刻还能
        用的法力，再挑出“没有硬币就付不起、且确实出现在后续动作里”的牌，取
        其中估价值最高的一张作为硬币收益。凑不出值得跳币的牌（或凑出来却没
        打）等于白丢一张牌，按 COIN_WASTE_PENALTY 计。

        参数口径：later 是排在硬币之后的**全部**动作（含序列里最后一个还没
        模拟进 state 的动作）。state 是“最后一个动作还没模拟”的场面，所以：
          * 硬币自己已经模拟过（later 非空）时，state.my_last_mana 里已经含
            硬币的 +1，先减回去才是打出硬币前的法力；
          * later 里除最后一个之外的费用已经在 state 里扣过，要加回来；
          * 最后一个动作的费用还没扣，不能加。
        """
        later = list(later or ())
        coin_cost = max(0, getattr(action, "cost", 0) or 0) if action else 0
        mana_before = max(0, getattr(state, "my_last_mana", 0) or 0)
        if later:
            mana_before -= 1                  # 硬币的 +1 已经被模拟进 state
        for item in later[:-1]:
            if item.action == ActionKind.PLAY_CARD:
                mana_before += max(0, getattr(item, "cost", 0) or 0)
        mana_before = max(0, mana_before)
        # 不跳币时这些法力本来就能用；跳币净得 1 费，但硬币自己也可能要付费
        # （喉魔光环下硬币变成 1 费，此时跳币根本不赚）。
        budget = mana_before - coin_cost + 1
        best = 0.0
        for item in later:
            if item.action != ActionKind.PLAY_CARD:
                continue
            card = getattr(item, "card", None)
            card_id = getattr(item, "card_id", None)
            if not card_id or Dwys._is_coin(card_id):
                continue
            cost = max(0, getattr(item, "cost", 0) or 0)
            if cost <= mana_before or cost > budget:
                continue              # 不跳币也出得起，或跳了币仍然付不起
            if (cost < COIN_ENABLED_MIN_COST
                    and card_id != CARD_ID_SUMMON_RITUAL):
                # 硬币留给出 3 费及以上的大怪（用户 2026-10-23 标注
                # 215156 t002-s0：不值得跳币出异教低阶牧师，硬币该留给
                # 3 费/4 费怪）。召唤仪式 BE_085 是标注要求的 2 费例外。
                continue
            if card is not None:
                best = max(best, Dwys._card_play_value(state, card))
        # 硬币只值“跳币换到的牌比不跳币能出的牌好多少”（用户 2026-10-25
        # 确认 160228：纯看价值）。160228 t004/t006 跳币出的卡扎库斯/扎卡兹
        # 并不比按曲线能出的召唤仪式/卡扎库斯更值，硬币等于白丢一张；
        # 202733 t006 跳币换到的扎卡兹明显强过 3 费喉魔，硬币就该加分。
        gain = (best - Dwys._best_on_curve_value(state, mana_before)
                if best >= COIN_ENABLED_MIN_GAIN else COIN_WASTE_PENALTY)
        if (getattr(state, "my_total_mana", 0) or 0) == 2 and any(
                item.action == ActionKind.PLAY_CARD
                and getattr(item, "card_id", None) == CARD_ID_KAZAKUS
                for item in later):
            # 2 费回合跳币出 3 费卡扎库斯：下回合只剩 3 费，接不上 4 费庭审
            # （CAP_405tb2），卡扎库斯的价值兑现不了（用户 2026-10-25 确认
            # 160228 t004：这个回合先按曲线出 2 费召唤仪式，不跳币）。
            gain -= COIN_KAZAKUS_TURN2_PENALTY
        return gain

    @staticmethod
    def _best_on_curve_value(state: AiState, mana: int) -> float:
        """不跳币时，这回合按曲线能打出的最好一张牌的估值。"""
        best = 0.0
        for card in getattr(state, "my_hand_cards", []) or []:
            if Dwys._is_coin(getattr(card, "card_id", None)):
                continue
            if Dwys._card_cost(card) > mana:
                continue
            best = max(best, Dwys._card_play_value(state, card))
        return best

    @staticmethod
    def _card_play_value(state: AiState, card) -> float:
        """估算一张还在手里的牌打出去值多少分（硬币解锁判断用）。"""
        cardtype = getattr(card, "cardtype", None)
        if cardtype not in ("MINION", "SPELL"):
            return 0.0
        action = AiAction(
            ActionKind.PLAY_CARD,
            source=None,
            cost=Dwys._card_cost(card),
            card_type=cardtype,
            card_id=getattr(card, "card_id", None),
            entity_id=getattr(card, "entity_id", None),
            card=card)
        if cardtype == "MINION":
            return Dwys._minion_gain(state, action, ())
        return Dwys._spell_gain(state, action, (), ())

    @staticmethod
    def _spell_gain(state: AiState, action: AiAction,
                    actions: list[AiAction],
                    later: list[AiAction] = ()) -> float:
        card_id = getattr(action, "card_id", None)
        context = Dwys._context(state)
        gain = 0.0

        if card_id == CARD_ID_CRYPT:                      # 咒怨之墓
            gain += 4.0
            gain += 2.0 if Dwys._first_play(actions) else -3.0
            # state 是这张牌自己的前缀场面，法力已经是“出它那一刻”的值：
            # 再按 later 把后续费用加回来会重复计算（20261026_204211 t002-s0：
            # 序列末尾补了结束回合后，咒怨被多算 2 费、跳过低费惩罚）。
            mana_left = max(0, (getattr(state, "my_last_mana", 0) or 0)
                            - max(0, getattr(action, "cost", 0) or 0))
            if mana_left <= 0:
                # 发现的是临时牌：剩余法力为 0 时打不出去，等于白扔咒怨之墓
                # （用户 2026-10-24 标注 t008-s2）。扣到低于场面分，搜索会
                # 保留“空动作”序列，执行层据此选择结束回合。
                gain += CRYPT_NO_MANA_PENALTY
            elif mana_left <= CRYPT_LOW_MANA_MAX:
                # 只剩 ≤2 费：临时牌基本付不起，这一手大概率白费
                # （用户 2026-10-25 确认 111710 t001-s0）。
                gain += CRYPT_LOW_MANA_PENALTY
            if (Dwys._has_strong_playable_card(state, later)
                    and not Dwys._first_play(actions)):
                # 手牌里还有 3 费及以上的牌可出、而且咒怨之墓不是本回合
                # 第一手：先把牌打了再找牌（用户 2026-10-23）。反过来，
                # 咒怨先出时要留够法力做发现，先出它不扣分（101941 t010-s1：
                # 人类先出 0 费咒怨，再打污亵）。
                gain += CRYPT_STRONG_HAND_PENALTY
        elif card_id == CARD_ID_CUTHUN_RITUAL:            # 克苏恩仪式
            # 2 回合后触发一项克苏恩技能，按当前最强的两项（凝视约 10-12、
            # 仆从约 14）折现给 9，早期启动再 +1（用户 2026-10-25 微调：
            # 越早出仪式，技能越早落地，111710 t001-s0 1 费回合要先出仪式）。
            gain += 10
            if state.oppo_minion_num >= 2:
                gain += 2.0
            if state.my_minion_num >= 2:
                gain += 2.0
            if context.ritual_pending > 0:
                gain -= 2.0
            if Dwys._enemy_lethal_next_turn(state):
                gain -= 50.0
        elif card_id == CARD_ID_TUTORIAL:                 # 漂浮教程
            gain += Dwys._tutorial_gain(state, action, actions, later)
        elif card_id == CARD_ID_ENTROPY:                  # 续连熵能
            gain += Dwys._entropy_gain(state, later)
        elif card_id == CARD_ID_SUMMON_RITUAL:            # 召唤仪式
            # 2 费换两个 3/5 嘲讽、2 回合后落地，人工按曲线首选
            # （用户 2026-10-25 确认 160228 t004：2 费回合直接出它，
            # 而不是硬币跳 3 费卡扎库斯）。
            gain += 20.0
            if Dwys._enemy_lethal_next_turn(state):
                gain -= 50.0
            if state.my_board_slot_num >= 6:
                gain -= 5.0
        elif card_id == CARD_ID_DEFILE:                   # 污亵
            gain += Dwys._defile_gain(state, action)
        elif card_id == CARD_ID_TRIAL_SLOW:               # 艰涩庭审
            # 庭审牌基础价值高（用户 2026-10-25 确认 160228 t008）：它是
            # “延迟 1 回合的高价值收益”，只要场面不紧急（下回合不会被
            # 斩杀）就该出；紧急时收益到不了，按低分计。
            # 场面压力线同样算紧急（用户 2026-10-25 标注 t008-s0）：
            # 有场面压力时优先解场，而不是出延迟有高价值的庭审。
            urgent = (Dwys._enemy_lethal_next_turn(state)
                      or Dwys._must_clear_now(state))
            gain += 8.0 if urgent else 40.0
        elif card_id == CARD_ID_TRIAL_FAST:               # 快速庭审
            # 快速庭审直接把庭审效果结算掉，等于一整套庭审收益；按高价值算（用户 2026-10-25 确认 152250 t014-s0）。
            gain += 56.0   # 无需考虑斩杀，有时候就是需要它的939来挡斩杀。
        elif card_id == CARD_ID_TRIAL_ENDLESS:            # 无尽庭审
            gain += 10.0
        if card_id in BOARD_BUFF_SPELL_IDS and Dwys._doomsayer_unkillable(state):
            # 末日预言者杀不掉：加的属性活不到下个回合，等于白花法力
            # （用户 2026-10-25 标注 t006-s0）。
            gain += DOOMSAYER_WASTE_PENALTY
        return gain

    @staticmethod
    def _tutorial_gain(state: AiState, action: AiAction,
                       actions: list[AiAction],
                       later: list[AiAction] = ()) -> float:
        """漂浮教程：使一个随从和手牌一张随机随从获得「2×费用」属性值。

        用户 2026-10-26 口径：指定目标那一半是确定的，由 doAction 模拟进场面
        （终局静态分体现，见 ai.card_rules），动作分不再计这份身材；手牌
        「随机一张随从」那一半仍是随机的，继续按期望值折算
        （见 _tutorial_hand_buff_gain：本回合会打出去的按全额，其余按
        TUTORIAL_HAND_BUFF_RATE 折现）。
        1 费出仍扣分（留着等加费更值）。

        实际费用要把序列里已出的喉魔算进去：喉魔光环让后续法术 +1 费，
        先喉魔再教程时按 3 费档计（+6/+6），这正是人类 t010-s0 的思路。
        """
        # 指定目标那一半（+2×费用/+2×费用）已由 doAction 精确模拟、由终局
        # 静态分体现，这里不再计身材分。两个例外：
        #   * 手牌「随机一张随从」那一半仍是随机的，保留期望值
        #     （用户 2026-10-26：随机性统一用动作分解决）；
        #   * 序列里先出喉魔、再贴教程时，doAction 看到的仍是出喉魔前的费用，
        #     品质只按原费用模拟；光环多出来的 1 费对应 +2/+2，补在动作分里。
        base_cost = int(getattr(action, "cost", 0) or 0)
        cost = base_cost + sum(
            1 for item in (actions or ())[:-1]
            if item.action == ActionKind.PLAY_CARD
            and getattr(item, "card_id", None) == CARD_ID_RITUALIST)

        gain = TUTORIAL_BOARD_TARGET_PER_COST * (cost - base_cost)
        marked = Dwys._pterrordax_drawn_card(
            state, list(actions or ()) + list(later or ()))
        discard_risk = (marked is not None
                        and Dwys._same_card(
                            getattr(action, "card", None), marked))
        if cost <= 1 and not discard_risk:
            # 栉龙抽到的教程带“易丢弃”标记：当回合用掉比留着等加费更值，
            # 不再扣“出太早”的分（用户 2026-10-25 确认 070147 t013-s2）。
            gain -= TUTORIAL_EARLY_PENALTY
        gain += Dwys._tutorial_hand_buff_gain(state, cost, later)

        target = Dwys._target_entity(state, action)
        if target is not None:
            health = getattr(target, "health", 0) or 0
            if (getattr(target, "taunt", 0) or getattr(target, "reborn", 0)
                    or health >= 4):
                gain += 2.0
            if health <= 1:
                gain -= 2.0
        if Dwys._played_minion_before(actions):
            gain += 2.0
        return gain

    @staticmethod
    def _tutorial_hand_buff_gain(state: AiState, cost: int,
                                 later: list[AiAction] = ()) -> float:
        """手牌随机那一半属值的期望收益（+2×费用攻 / +2×费用血 = 6 分/费）。

        随机落到手牌里的哪张随从是未知的，所以按手牌随从数量取平均：
          * 本回合真的会打出去的那张按全额——它的属性会直接变成场面分，
            而手牌随机那一半没有被 doAction 模拟，这份属性只有在这里补；
            战吼会召唤自身复制
            的随从（灾毁迅疾幼龙）要算两份，本体和复制体都继承这份属性
            （用户 2026-10-25 指出：这才是教程先手的真实收益来源）；
          * 其余的按 TUTORIAL_HAND_BUFF_RATE 折现——属性留在手牌上，
            本回合变不成场面。
        手牌一律按「打出教程那一刻」算：评分拿到的 state 是序列结束时的场面，
        序列里排在教程之后落场的随从已经离开手牌，必须补回来。否则“先出随从、
        再贴教程”会低估教程（随机 buff 本可落到紧接着打出的那张牌上），
        而“先贴教程、再下随从”会被算成手牌里只剩打不出去的牌
        （111710 t011-s0 人类备注 + 用户 2026-10-25）。
        """
        if cost <= 0:
            return 0.0
        hand_minions = [
            card for card in (getattr(state, "my_hand_cards", []) or [])
            if getattr(card, "cardtype", None) == "MINION"]
        played = [item.card for item in (later or ())
                  if item.action == ActionKind.PLAY_CARD
                  and getattr(item, "card_type", None) == "MINION"
                  and item.card is not None]
        for card in played:
            if not any(Dwys._same_card(card, other) for other in hand_minions):
                hand_minions.append(card)
        if not hand_minions:
            return 0.0
        per_card = TUTORIAL_BOARD_TARGET_PER_COST * cost
        tears = Dwys._context(state).tears_in_deck
        total = 0.0
        for card in hand_minions:
            if any(Dwys._same_card(card, other) for other in played):
                # 战吼召唤自身复制：一次 buff 换来本体 + 复制两份身材
                copied = (getattr(card, "card_id", None) == CARD_ID_DRAKE
                          and tears > 0)
                total += per_card * (2.0 if copied else 1.0)
            else:
                total += per_card * TUTORIAL_HAND_BUFF_RATE
        return total / len(hand_minions)

    @staticmethod
    def _entropy_gain(state: AiState, later: list[AiAction] = ()) -> float:
        """续连熵能：+1/+1 身材增益已由 doAction 模拟，这里只保留决策分。

        +1/+1 的收益 原本规则分 改由模拟后的终局静态分体现
        如果血量过低，考虑掉加2张撕裂，扣点分
        """
        count = state.my_minion_num - Dwys._minions_played_in(later)
        count = max(0, count)
        gain = 0.0
        if count == 0:
            gain -= 50.0
        elif count == 1:
            gain -= 3.0
        health = getattr(state.my_hero, "health", 30) or 0
        if health <= 6:
            gain -= 3.0
        return gain

    # ---------------------------------------------------------------- 斩杀判定
    @staticmethod
    def lethal_action(state: AiState, action: AiAction) -> bool:
        """这一个动作自己就能打死敌方英雄：搜索立即收工并采用这条线。

        用户 2026-10-25 要求：不再给“单卡斩杀”额外加分，改由搜索发现能
        斩杀就结束整个搜索。只认“这一下直接打死”（随从打脸 / 单卡直伤），
        不认还差一步才凑够的组合斩杀——那种情况人类会先把伤害打出去、把
        收尾牌留到最后（125102 t012-s0/s1 先打脸、s2 才污亵收尾）。
        """
        hero = getattr(state, "oppo_hero", None)
        if hero is None:
            return False
        health = max(0, getattr(hero, "health", 0) or 0)
        if health <= 0:
            return False
        if action.action == ActionKind.ATTACK:
            attacker = getattr(action, "card", None)
            target = getattr(action, "target", None)
            return (attacker is not None and target is not None
                    and target.kind == "hero"
                    and (getattr(attacker, "attack", 0) or 0) >= health)
        if (action.action == ActionKind.PLAY_CARD
                and getattr(action, "card_type", None) == "SPELL"):
            return Dwys._face_spell_damage(state, action) >= health
        return False

    @staticmethod
    def lethal_ceiling(state: AiState, remaining_mana: int = 0) -> int:
        """本回合还能打在敌方英雄脸上的最大伤害（只给搜索定优先级，不计分）。

        用户 2026-10-28 要求：不想靠调大 MAX_SEARCH 阈值来找斩杀线，改由策略
        告诉搜索“这条线还差多少能斩杀”，搜索把够斩杀的节点优先级顶到最高。

        口径尽量保守（只当上限用，宁可多抬优先级也不漏掉斩杀线）：
          * 敌方还有嘲讽随从时，场攻这回合打不到脸，按 0 算；
          * 随从要能出手（排除 exhausted / frozen / dormant / cant_attack），
            风怒算两次；
          * 直伤只有污亵：付得起时按“是不是手牌最高费”给 4 或 2 点。
        """
        total = 0
        taunted = any(
            (getattr(minion, "health", 0) or 0) > 0
            and getattr(minion, "taunt", 0)
            for minion in getattr(state, "oppo_minions", []) or [])
        if not taunted:
            for minion in getattr(state, "my_minions", []) or []:
                if (getattr(minion, "frozen", 0)
                        or getattr(minion, "dormant", 0)
                        or getattr(minion, "cant_attack", 0)):
                    continue
                attack = max(0, getattr(minion, "attack", 0) or 0)
                # 风怒能出手两次，但要扣掉本回合已经打掉的次数：打完一次后
                # 只剩一击，别再把斩杀希望算成满值（2026-10-30 190729 t016-s2，
                # 否则每个攻击方向都被判成“还够斩杀”，方向选择反而看不到脸）。
                allowed = 2 if getattr(minion, "windfury", 0) else 1
                used = int(getattr(minion, "attacks_this_turn", 0) or 0)
                if getattr(minion, "exhausted", 0) and not used:
                    # 真实局面只有 EXHAUSTED 标签（= 本回合的次数用完了），
                    # 搜索里没模拟过的随从按“已用完”处理，与旧口径一致。
                    used = allowed
                left = allowed - used
                if left <= 0:
                    continue
                total += attack * left
        budget = max(0, int(remaining_mana or 0))
        hand = list(getattr(state, "my_hand_cards", []) or [])
        for card in hand:
            if getattr(card, "card_id", None) != CARD_ID_DEFILE:
                continue
            if Dwys._card_cost(card) > budget:
                continue
            top = max((Dwys._card_cost(other) for other in hand
                       if not Dwys._same_card(other, card)), default=0)
            total += 4 if Dwys._card_cost(card) >= top else 2
        return total

    @staticmethod
    def _face_spell_damage(state: AiState, action: AiAction) -> int:
        """这张法术直接打到敌方英雄脸上的伤害（只覆盖本卡组的直伤牌）。"""
        if getattr(action, "card_id", None) == CARD_ID_DEFILE:
            return Dwys._defile_damage(state, action)
        return 0

    @staticmethod
    def _defile_damage(state: AiState, action: AiAction) -> int:
        """污亵：本牌是手牌里最贵的一张时打两波（合计 4 点），否则 2 点。"""
        damage = 4
        cost = action.cost
        for card in getattr(state, "my_hand_cards", []) or []:
            if Dwys._same_card(card, action.card):
                continue
            if Dwys._card_cost(card) > cost:
                damage = 2
                break
        return damage

    @staticmethod
    def _defile_gain(state: AiState, action: AiAction) -> float:
        """污亵：伤害已由 doAction 精确模拟、由终局静态分体现，这里只补
        「搜索看不懂」的战略分（用户 2026-10-26 口径，agents.md 原则 4）：

          * 法术打死的敌人在优先解列表里 -> 按表加策略分。
        斩杀不再单独计分：终局静态分统一按“敌方英雄生命归零”给
        LETHAL_BONUS，不论伤害来自单卡还是多张牌累计（用户 2026-10-28）。

        击杀价值、未死随从伤害、场攻补刀、打脸伤害都不再计分——终局场面
        静态分已经体现过一次。
        """
        gain = -8.0 # ann-20261030_115037_动物园术-t008-s0 避免随便就出了
        if Dwys._defile_damage(state, action) == 2:
            # 不是手牌最高费：卡面的“重复一次”不触发，只打 2 点而不是 4 点，
            # 收益明显缩水（用户 2026-10-28：这种情况打出污亵扣 1 分）。
            gain += DEFILE_NOT_TOP_PENALTY
        for card_id in defile_killed_card_ids(state, action.card):
            gain += PRIORITY_KILL_SCORES.get(card_id, 0.0)
        return gain

    @staticmethod
    def _zakaz_discount_gain(state: AiState, action=None) -> float:
        """腐蚀者扎卡兹的减费价值：手牌随从各 -1 费，逐张折现。

        - 每张手牌随从按 1 费 = ZAKAZ_DISCOUNT_PER_MINION 计（搜索不模拟减费，
          只在这里一次性折现）；
        - 减费后“下回合正好出得起”的随从额外加一点：这一费就是解锁费；
        - 1 费减到 0 费的随从随时可以甩出去，也算解锁。
        """
        budget_next = min(10, (getattr(state, "my_total_mana", 0) or 0) + 1)
        total = 0.0
        for card in getattr(state, "my_hand_cards", []) or []:
            if getattr(card, "cardtype", None) != "MINION":
                continue
            if action is not None and Dwys._same_card(card, action.card):
                continue
            cost = Dwys._card_cost(card)
            new_cost = max(0, cost - 1)
            total += ZAKAZ_DISCOUNT_PER_MINION
            if cost > budget_next >= new_cost:
                total += ZAKAZ_DISCOUNT_UNLOCK_BONUS
            elif new_cost == 0:
                total += ZAKAZ_DISCOUNT_FREE_BONUS
        return min(ZAKAZ_DISCOUNT_CAP, total)

    @staticmethod
    def _minion_gain(state: AiState, action: AiAction,
                     actions: list[AiAction]) -> float:
        """随从动作分 = 逐卡效果/条件增量（身材由终局静态分负责）。

        用户 2026-10-26：最终序列得分 = 终局场面静态分 + 动作分，身材不能
        在动作分里再加一次，否则 4/3 随从会被两处各算一遍。
        """
        card_id = getattr(action, "card_id", None)
        context = Dwys._context(state)
        gain = 0.0

        if card_id == CARD_ID_EGG:                      # 暮光龙卵
            # 亡语召唤 2/1 雏龙，搜索不模拟，按雏龙身材价值折现。
            gain += 5.0
        elif card_id == CARD_ID_PTERRORDAX:               # 栉龙
            if len(actions) == 1:
                gain += 2.0
            gain -= 2.5                                   # 弃牌风险
        elif card_id in (CARD_ID_CULT_PRIEST,
                         CARD_ID_CULT_PRIEST_CORE):       # 异教低阶牧师
            pass                                          # 只按身材
        elif card_id == CARD_ID_HERALD:                   # 暗誓信徒
            gain += 2.0                                   # 士兵 1/1 + 亡语回 3
        elif card_id == CARD_ID_TIMEWARP:                 # 暮光时空跃迁者
            if Dwys._hand_has(state, CARD_ID_DRAKE):
                gain += 3.0
        elif card_id == CARD_ID_RITUALIST:                # 喉魔异教徒
            if Dwys._hand_has(state, CARD_ID_TUTORIAL):
                gain += 2.0
        elif card_id == CARD_ID_KAZAKUS:                  # 教父卡扎库斯
            # 战吼“筹备庭审”本身不改变场面，价值全在下回合的庭审牌上：
            # 按当前场面最好的两个庭审效果折现，封顶 KAZAKUS_SETUP_CAP。
            effects = sorted(
                (Dwys._trial_effect_gain(state, trial_id)
                 for trial_id in _TRIAL_PRIORITY),
                reverse=True)
            gain += min(KAZAKUS_SETUP_CAP,
                        KAZAKUS_SETUP_DISCOUNT * sum(effects[:2]))
            if Dwys._enemy_lethal_next_turn(state):
                gain -= 3.0
        elif card_id == CARD_ID_ZAKAZ:                    # 腐蚀者扎卡兹
            # 战吼给手牌和牌库的随从 -1 费：搜索不模拟减费，按手牌随从逐张
            # 折现（用户 2026-10-25 确认：扎卡兹除了身材，还要看减费价值）。
            gain += Dwys._zakaz_discount_gain(state, action)
            if Dwys._hand_has(state, CARD_ID_TUTORIAL):
                gain += ZAKAZ_TUTORIAL_BONUS
            if Dwys._hand_has(state, CARD_ID_DRAKE):
                gain += 3.0
            if Dwys._hand_has(state, CARD_ID_DEFILE):
                gain += 2.0
            if Dwys._zakaz_unlocks_kishix(state):
                # 减费让条件已满足的基希克斯提前一回合上场（111710 t007-s1）。
                gain += ZAKAZ_KISHIX_EARLY_BONUS
        elif card_id == CARD_ID_VAULT_GUARD:              # 库区守卫 BE_151
            # 战吼/亡语 +2/+2 给相邻随从，搜索不模拟：按最佳内侧落点的
            # 相邻收益折现计入出牌分（用户 2026-10-24）。
            try:
                entries = ordered_board(state, "friendly")
            except Exception:
                entries = []
            # 搜索模拟已经把它自己放到场上（最右），算相邻收益时要去掉自己。
            entries = [
                entry for entry in entries
                if not (entry.kind == "minion"
                        and getattr(entry.entity, "card_id", None)
                        == CARD_ID_VAULT_GUARD)]
            _gap, buff = Dwys._vault_guard_best_gap(state, entries)
            if buff > 0:
                gain += min(VAULT_GUARD_BUFF_CAP,
                            VAULT_GUARD_BUFF_DISCOUNT * buff)
        elif card_id in (CARD_ID_GLACIER,
                         CARD_ID_GLACIER_LEGACY):         # 冰川裂片
            freeze = Dwys._glacier_freeze_gain(state, action, actions)
            if freeze < GLACIER_FREEZE_USELESS:
                # 冻不到有威胁的目标：整牌按冻结收益减 4 计（用户 2026-10-25）。
                # 身材已由终局静态分负责，这里不再重复加减。
                gain = freeze + GLACIER_USELESS_PENALTY
            else:
                gain += freeze
        elif card_id == CARD_ID_SAVIS:                    # 梦魇之王萨维斯
            # 战吼“牌库发现随从 + 黑暗之赐”不模拟结算，按一次手牌资源收益
            # 折现；发现菜单本身沿用通用发现评分（见 _discover_gain）。
            gain += SAVIS_BATTLECRY_GAIN
        elif card_id == CARD_ID_REBORN_PTERRORDAX:        # 重生的翼手龙
            # 卡面「消耗残骸而非法力值」：不占本回合法力，把省下的法力折回
            # 牌本身（101941 t014-s1 人类备注：用于解场，还不占用费用）。
            gain += Dwys._card_cost(action.card)
        elif card_id == CARD_ID_KISHIX:                   # 基希克斯
            # 战吼前置：本局完成过仪式才能开启罪孽仪式；延迟 2 回合结算，
            # 敌方下回合可斩杀时收益归零。
            if context.ritual_completed and not Dwys._enemy_lethal_next_turn(state):
                gain += KISHIX_RITUAL_SETUP
        # 灾毁迅疾幼龙不再给动作分：战吼召唤的那只复制体已经由 doAction
        # 模拟进场面（ai.card_rules.summon_battlecry_copy），终局静态分会把
        # 两只 5/5 的身材一起算上；再补动作分就是同一份身材重复计分
        # （用户 2026-10-30 t010-s0：要的是场面模拟正确、得分正确）。
        if Dwys._doomsayer_unkillable(state):
            # 末日预言者本回合杀不掉：下个回合开始所有随从被清光，
            # 这回合出的任何随从都是白送（用户 2026-10-25 标注 t006-s0）。
            gain += DOOMSAYER_WASTE_PENALTY
        return gain

    # ---------------------------------------------------------------- 选择菜单
    @staticmethod
    def _discover_gain(state: AiState, action: AiAction) -> float:
        """发现/选择：general_choice_cards 是日志 0 基下标，号位是 1 基。"""
        card_id = Dwys._choice_card_id(state, action)
        if not card_id or card_id not in CARD_ATTRIBUTES:
            return DISCOVER_UNKNOWN
        if card_id in (CARD_ID_CUTHUN_FURY, CARD_ID_CUTHUN_DOMAIN,
                       CARD_ID_CUTHUN_GAZE, CARD_ID_CUTHUN_SERVANT):
            return Dwys._cuthun_choice_gain(state, card_id, action)
        if card_id in (CARD_ID_TRIAL_FAST, CARD_ID_TRIAL_FAST_OPTION,
                       CARD_ID_TRIAL_SLOW, CARD_ID_TRIAL_SLOW_OPTION,
                       CARD_ID_TRIAL_ENDLESS,
                       CARD_ID_TRIAL_ENDLESS_OPTION):
            return Dwys._trial_length_gain(state, card_id)
        if card_id in _TRIAL_PRIORITY:
            return Dwys._trial_effect_gain(state, card_id)
        if card_id in (CARD_ID_SIN_FAMINE, CARD_ID_SIN_DOMINION,
                       CARD_ID_SIN_CONQUEST):
            return Dwys._sin_ritual_gain(state, card_id)
        return Dwys._generic_discover_gain(state, card_id)

    @staticmethod
    def _sin_ritual_gain(state: AiState, card_id) -> float:
        """罪孽仪式菜单：三个选项都是 2 回合后结算的延迟收益。

        同分优先级：征服 +0.03 > 支配 +0.02 > 饥荒 +0.01，只破同分。
        敌方下回合可斩杀时统一 -50：菜单必须选，但收益救不了场。
        """
        #if Dwys._enemy_lethal_next_turn(state):
        #   return -50.0
        if card_id == CARD_ID_SIN_CONQUEST:               # 征服仪式：各处随从 +6/+6
            minions = ((getattr(state, "my_minion_num", 0) or 0)
                       + Dwys._hand_minion_count(state))
            gain = 4 + minions * 6
            return gain + 0.03
        if card_id == CARD_ID_SIN_DOMINION:               # 支配仪式：召唤 3 个传说
            free = max(0, 7 - (getattr(state, "my_board_slot_num", 0) or 0))
            if free >= 3:
                gain = 30
            elif free == 2:
                gain = 20
            elif free == 1:
                gain = 5.0   #因为浪费
            else:
                gain = 0    # 因为浪费，但现在没格子，未必两回合后没有
            return gain + 0.02
        # 饥荒仪式：从对手牌库抽五张，延迟兑现。 不认识牌，对ai不友好，优先级低
        gain = 5.0
        return gain + 0.01

    # ------------------------------------------------------------ 克苏恩本体
    @staticmethod
    def _cuthun_body(state: AiState, action=None):
        """我方站场的克苏恩，帝国之眼（BE_013）本体；没有返回 None。"""
        card = getattr(action, "card", None) if action is not None else None
        if (card is not None
                and getattr(card, "card_id", None) == CARD_ID_CUTHUN):
            return card
        for minion in getattr(state, "my_minions", []) or []:
            if getattr(minion, "card_id", None) == CARD_ID_CUTHUN:
                return minion
        return None

    @staticmethod
    def _cuthun_gaze_usable(state: AiState) -> bool:
        """凝视还有没有合法目标（免疫/不可触碰/休眠/潜行/扰魔的敌方随从不能点）。"""
        for minion in getattr(state, "oppo_minions", []) or []:
            if any(getattr(minion, flag, 0) for flag in (
                    "immune", "untouchable", "dormant", "stealth",
                    "not_targeted_by_spell")):
                continue
            return True
        return False

    @staticmethod
    def _cuthun_has_usable_ability(state: AiState, body) -> bool:
        """本体是否还有「现在就能点」的未用技能（与搜索候选生成同口径）。"""
        used = set(getattr(body, "ability_used", ()) or ())
        for index, ability_id in enumerate(CUTHUN_ABILITY_ORDER):
            if index in used:
                continue
            if ability_id == CARD_ID_CUTHUN_DOMAIN:       # 领域：要另一个友方
                if len(getattr(state, "my_minions", []) or []) < 2:
                    continue
            elif ability_id == CARD_ID_CUTHUN_SERVANT:    # 仆从：要有空格
                if (getattr(state, "my_board_slot_num", 0) or 0) >= 7:
                    continue
            elif ability_id == CARD_ID_CUTHUN_GAZE:       # 凝视：要有合法目标
                if not Dwys._cuthun_gaze_usable(state):
                    continue
            return True
        return False

    @staticmethod
    def _minion_ability_gain(state: AiState, action: AiAction,
                             actions) -> float:
        """克苏恩本体技能评分：固定大额激活分 + 随机技能效果补偿分。

        除非用不了，默认每回合用满 2 次：每次激活都给大额固定分，同回合第
        2 次再加一档。技能效果只给随机结果（愤怒/领域）补期望值，弥补
        doAction 的确定化模拟误差；凝视/仆从已被 doAction 精确模拟，最终
        场面分里已经算过，动作分不再重复加分（agents.md 原则 4）。
        """
        body = Dwys._cuthun_body(state, action)
        uses = int(getattr(body, "ability_uses_turn", 0) or 0) if body else 0
        gain = CUTHUN_ABILITY_ACTIVATE_BONUS
        if uses >= 1:
            gain += CUTHUN_ABILITY_SECOND_USE_BONUS
        ability = ability_spec(getattr(action, "choice_card_id", None))
        if ability is not None and ability.random_outcome:
            gain += Dwys._cuthun_choice_gain(
                state, getattr(action, "choice_card_id", None), action)
        return gain

    @staticmethod
    def _cuthun_end_turn_penalty(state: AiState) -> float:
        """本体还有可用技能却要提前结束回合：重扣，保证优先烧技能。"""
        body = Dwys._cuthun_body(state)
        if body is None:
            return 0.0
        if (int(getattr(body, "ability_uses_turn", 0) or 0)
                >= CUTHUN_ABILITY_MAX_PER_TURN):
            return 0.0
        if not Dwys._cuthun_has_usable_ability(state, body):
            return 0.0
        return CUTHUN_ABILITY_END_TURN_PENALTY

    @staticmethod
    def _cuthun_choice_gain(state: AiState, card_id, action=None) -> float:
        enemy_count = state.oppo_minion_num
        my_count = state.my_minion_num
        if card_id == CARD_ID_CUTHUN_FURY:                # 愤怒：7 伤随机分配
            gain = 9.0 if enemy_count >= 3 else (7.0 if enemy_count == 2 else 4.0)
            for minion in getattr(state, "oppo_minions", []):
                if ((getattr(minion, "attack", 0) or 0) >= 5
                        and 0 < (getattr(minion, "health", 0) or 0) <= 2):
                    gain += 8.0
                    break
            return gain
        if card_id == CARD_ID_CUTHUN_DOMAIN:              # 领域：两个友方 +2/+3
            if my_count >= 3:
                return 15.0
            return 11.0 if my_count == 2 else 0.0
        if card_id == CARD_ID_CUTHUN_GAZE:                # 凝视：5 伤 + 吸血
            # 选完选项还要点一个敌方随从：按实际目标评分，才能挑对那只。
            target = (Dwys._target_entity(state, action)
                      if action is not None else None)
            if target is not None:
                # 蛋：打它只会加速孵化，重罚（用户 2026-10-24）。
                if Dwys._is_unhatched_egg(target):
                    return ATTACK_EGG_PENALTY
                # 用户 2026-10-24 确认：同分时优先右侧号位（用号位极小量打破
                # 并列）。0.01 远小于任何实质收益差，不改变收益比较结果。
                tie_break = (0.01 * (action.target.index or 0)
                             if action.target is not None else 0.0)
                health = max(0, getattr(target, "health", 0) or 0)
                if 0 < health <= 5:
                    return Dwys._minion_value(target, mine=False) + tie_break
                return 5.0 + tie_break  # 打不死也是 5 点伤害 + 吸血 5
            best = 0.0
            for minion in getattr(state, "oppo_minions", []):
                if Dwys._is_unhatched_egg(minion):
                    continue                              # 蛋不在考虑范围
                if 0 < (getattr(minion, "health", 0) or 0) <= 5:
                    best = max(best, Dwys._minion_value(minion, mine=False))
            return best if best > 0 else 4.0
        # 仆从：两个 2/3 嘲讽
        if my_count <= 1:
            return 14.0
        if my_count <= 5:
            return 9.0
        return -5.0

    @staticmethod
    def _trial_effect_gain(state: AiState, card_id) -> float:
        hand_minions = Dwys._hand_minion_count(state)
        if card_id == CARD_ID_TRIAL_BLADE:                # 可疑快刀 +3/+3
            gain = min(6.0 * state.my_minion_num, 20.0)
            gain += min(3.0 * hand_minions, 8.0)
        elif card_id == CARD_ID_TRIAL_PACT:               # 邪恶协议：三个 3 费
            free_slots = 7 - state.my_board_slot_num
            gain = 13.0 if free_slots >= 3 else (7.0 if free_slots == 2 else 0.0)
        elif card_id == CARD_ID_TRIAL_CONTRABAND:         # 违禁物品：抽三张
            gain = 6.0
        elif card_id == CARD_ID_TRIAL_FORGERY:            # 假证精华：手牌随从 -2
            gain = min(2.0 * hand_minions, 6.0)
        elif card_id == CARD_ID_TRIAL_CONSPIRE:           # 合谋获刑：夺一个随从
            enemies = list(getattr(state, "oppo_minions", []) or [])
            # 随机夺取只按平均价值的 0.7 计：夺来的随从可能被对手反解，
            # 也不如暴君补剂稳定（用户 2026-10-24：暴君优先级高于合谋）。
            gain = (sum(Dwys._minion_value(m, mine=False) for m in enemies)
                    / len(enemies) * 0.7) if enemies else 0.0
        elif card_id == CARD_ID_TRIAL_SMUGGLE:            # 走私罪责：偷两张
            gain = 10.0
        elif card_id == CARD_ID_TRIAL_TYRANT:             # 暴君补剂：3/9 嘲讽
            # 用户 2026-10-24 确认：庭审菜单里暴君补剂永远优先。可疑快刀
            # （+3/+3）在满场时最高 20 + 8 = 28 分，这里取 30 保证它排第一
            # （190237 t007-s2：6 只随从场面仍应选暴君而非快刀）。
            gain = 30.0
        elif card_id == CARD_ID_TRIAL_POTION:             # 诱导饮剂：回 12
            health = getattr(state.my_hero, "health", 30) or 0
            if health <= 10:
                gain = 12.0
            elif health <= 15:
                gain = 8.0
            elif health <= 22:
                gain = 4.0
            else:
                gain = 1.0
        elif card_id == CARD_ID_TRIAL_DETAIN:             # 拘留待毁：随从互殴
            enemy_count = state.oppo_minion_num
            my_count = state.my_minion_num
            if enemy_count > my_count:
                gain = 6.0
            elif enemy_count == my_count:
                gain = 2.0
            else:
                gain = -10.0
        else:
            gain = 0.0
        # 同分优先级：只加很小的量，保证不改变量级。
        gain += 0.01 * _TRIAL_PRIORITY.get(card_id, 0)
        return gain

    @staticmethod
    def _trial_length_gain(state: AiState, card_id) -> float:
        # 第三轮菜单下发的是选项版 card_id（CAP_405tbXb），评分与正式版一致。
        if card_id in (CARD_ID_TRIAL_SLOW, CARD_ID_TRIAL_SLOW_OPTION):
            return 10.0                                    # 艰涩庭审：默认
        
        if card_id in (CARD_ID_TRIAL_FAST, CARD_ID_TRIAL_FAST_OPTION):
            high_cost = (getattr(state, "my_total_mana", 0) or 0) >= 6
            if high_cost:
                return 12.0
            else:
                return 2.0
        return 1.0                                        # 无尽庭审


    @staticmethod
    def _generic_discover_gain(state: AiState, card_id) -> float:
        """咒怨之墓 / 发现菜单：优先拿低费、能当回合打出的牌。

        选到本卡组自己的随从时，复用它的出牌专属加成（幼龙 +16、卡扎库斯
        筹备庭审等），身材仍按通用的一半折现，避免同一份身材算两次
        （用户 2026-10-25 确认 111710 t013-s1 / 101941 t010-s2）。
        """
        gain = 5.0
        item = card_item(card_id)
        is_minion = (item.get("type") or "") == "MINION"
        bonus = 0.0
        if is_minion:
            attack, health = item_stats(card_id)
            gain += 0.5 * (attack * ATTACK_WEIGHT + health * HEALTH_WEIGHT)
            bonus = max(0.0, Dwys._choice_minion_bonus(state, card_id))
            gain += bonus
            gain += Dwys._discover_rush_tempo_gain(
                state, card_id, attack, health)
        else:
            gain += 2.0
        if card_id in (CARD_ID_CUTHUN_RITUAL, CARD_ID_SUMMON_RITUAL):
            gain += 6.0
        if card_id == CARD_ID_DRAKE:
            # 旧口径给幼龙固定 +8（牌库没有撕裂时也有这个下限）。
            gain += max(0.0, 8.0 - bonus)
        try:
            cost = int(item.get("cost", 9) or 9)
        except (TypeError, ValueError):
            cost = 9
        temp_source = Dwys._discover_is_temporary(state)
        if cost <= max(0, getattr(state, "my_last_mana", 0) or 0):
            gain += 2.0
            if temp_source:
                # 咒怨之墓来源：本回合打得出的选项才留得住（用户 2026-10-28
                # 要求把原来的 -30 改成这项正向加分）。
                gain += CRYPT_CASTABLE_BONUS
        elif temp_source:
            # 本回合付不起的临时牌，回合结束前就消失，等于拿不到手：
            # 按 0 计（用户 2026-10-28 确认），不做扣分。
            return 0.0
        return gain

    @staticmethod
    def _discover_is_temporary(state: AiState) -> bool:
        """当前发现菜单是否是“临时牌”来源（咒怨之墓 TLC_451）。

        state 没有“发现来源”字段，用选项构成判断：咒怨之墓从整个牌库发现，
        会混入法术/武器等非随从选项；萨维斯 EDR_856 的发现只给随从牌。
        选项全是随从且我方场上已有萨维斯时按永久处理，否则保守按临时处理。
        """
        options = getattr(state, "general_choice_cards", None) or {}
        card_ids = [str(value) for value in options.values() if value]
        if not card_ids:
            return True
        for card_id in card_ids:
            if str(card_item(card_id).get("type") or "").upper() != "MINION":
                return True
        if any(getattr(minion, "card_id", None) == CARD_ID_SAVIS
               for minion in getattr(state, "my_minions", []) or []):
            return False
        return True

    @staticmethod
    def _choice_minion_bonus(state: AiState, card_id) -> float:
        """菜单里的随从选项：只取“专属效果”那一段分。

        _minion_gain 只算逐卡效果/条件增量（身材由终局静态分负责），所以这里
        直接返回它，不再扣身材——否则会把专属效果分整段扣没（111710 t007-s1：
        卡扎库斯/扎卡兹的筹备、减费收益被身材抵消）。
        """
        card = Dwys._fake_hand_card(card_id)
        if card is None or card.cardtype != "MINION":
            return 0.0
        action = AiAction(
            ActionKind.PLAY_CARD,
            f"发现（{card_name(card_id) or card_id}）",
            source=AiSlot("hand", "friendly", 1),
            cost=Dwys._card_cost(card),
            card_type="MINION",
            card_id=card_id,
            card=card)
        return Dwys._minion_gain(state, action, ())

    @staticmethod
    def _discover_rush_tempo_gain(state: AiState, card_id,
                                  attack: int, health: int) -> float:
        """发现菜单里的突袭/冲锋随从：当回合能直接解掉的敌方随从价值。

        用户 2026-10-25 确认 205200 t011-s1：人类在三选一里选突袭幼龙而不是
        价值更高、但要等一回合才生效的卡扎库斯，理由就是“抢节奏/立即清场”。
        只有本回合费用够、且场上有能撞死的目标时才算收益；圣盾撞不死不算。
        撞完自己活下来按目标全价计，同归于尽只算双方价值差（为负不计）。
        """
        if not (item_flag(card_id, "rush") or item_flag(card_id, "charge")):
            return 0.0
        if attack <= 0 or health <= 0:
            return 0.0
        try:
            cost = int(card_item(card_id).get("cost", 9) or 9)
        except (TypeError, ValueError):
            return 0.0
        if cost > max(0, getattr(state, "my_last_mana", 0) or 0):
            return 0.0
        enemies = list(getattr(state, "oppo_minions", []) or [])
        if not enemies:
            return 0.0
        # 有嘲讽时只能先撞嘲讽，别把打不到的目标算成可解。
        taunts = [enemy for enemy in enemies if getattr(enemy, "taunt", 0)]
        targets = taunts or enemies
        our_value = Dwys._minion_value(
            Dwys._fake_hand_card(card_id), mine=False)
        best = 0.0
        for enemy in targets:
            if getattr(enemy, "divine_shield", 0):
                continue                    # 一下撞不死
            if attack < max(0, getattr(enemy, "health", 0) or 0):
                continue
            value = Dwys._minion_value(enemy, mine=False)
            if attack < max(0, getattr(enemy, "attack", 0) or 0):
                value -= our_value          # 同归于尽：只算交换差额
            best = max(best, value)
        return max(0.0, best) * RUSH_DISCOVER_TEMPO

    @staticmethod
    def _fake_hand_card(card_id):
        """菜单选项的替身手牌实体：只用于复用出牌评分，不参与搜索模拟。"""
        item = card_item(card_id)
        card_type = str(item.get("type") or "").upper()
        if not card_type:
            return None
        attack, health = item_stats(card_id)
        return SimpleNamespace(
            entity_id=None, card_id=card_id, zone="HAND",
            cardtype=card_type, current_cost=int(item.get("cost", 0) or 0),
            name=item.get("name") or "", attack=attack, health=health,
            damage=0, max_health=health, taunt=0, divine_shield=0, stealth=0,
            windfury=0, poisonous=0, life_steal=0, reborn=0, rush=0, charge=0,
            frozen=0, dormant=0, cant_attack=0, exhausted=0, temporary=0)

    # ---------------------------------------------------------------- 英雄技能
    @staticmethod
    def _hero_power_gain(state: AiState, action: AiAction,
                         actions: list[AiAction]) -> float:
        """术士生命分流：抽一张、扣 2 血。

        用户要求（2026-10-29 更新）：
          * 分流的所有加分（抽牌价值、本回合第一个动作 +1、满场/威胁/空场）
            统一在序列级规则 _hero_power_draw_bonus 里算一次，这里只保留
            “血量不安全就别用”的扣分，避免同一个分流被记成 +2 和 +9 两遍；
          * 先扣 2 血再评估安全性：已经进入对方场攻斩杀线时坚决不用。
        """
        gain = 0.0
        health = getattr(state.my_hero, "health", 30) or 0
        # 斩杀判断只看“打到脸的伤害”：嘲讽随从会先吸收对方场攻
        # （用户 2026-10-25 确认 t015-s0：两个大嘲讽在场时可以安全分流）。
        enemy_damage = Dwys._enemy_face_damage_next_turn(state)
        if health <= 8 or health - 2 <= enemy_damage:
            gain -= 100.0
        elif health <= 12 or health - 2 - enemy_damage <= 3:
            gain -= 4.0
        return gain

    # ---------------------------------------------------------------- 攻击
    @staticmethod
    def _attack_gain(state: AiState, action: AiAction) -> float:
        """打脸按伤害算（斩杀给天价），随从交换按换血/价值算。"""
        attacker = action.card
        target = action.target
        if attacker is None or target is None:
            return 0.0
        attack = getattr(attacker, "attack", 0) or 0
        if attack <= 0:
            return 0.0

        defender = Dwys._board_minion(state, "enemy", target.index)
        if defender is None:
            return 0.0
        # 凯洛斯的蛋：打它只会让对面更快孵出 20/20，任何随从都不许碰
        # （用户 2026-10-24）。未破壳的蛋没有嘲讽，打脸总是可选的。
        if Dwys._is_unhatched_egg(defender):
            return ATTACK_EGG_PENALTY
        attacker_health = getattr(attacker, "health", 0) or 0
        defender_health = getattr(defender, "health", 0) or 0
        if attacker_health <= 0 or defender_health <= 0:
            return 0.0

        # 换血、击杀价值、破盾、攻击者死亡价值都已由终局静态分体现
        # （用户 2026-10-26：动作分不得重复计分），这里只保留未来价值分。
        gain = 0.0
        defender_shield = bool(getattr(defender, "divine_shield", 0))
        if defender_shield:
            dealt = 0
            killed = False
        else:
            dealt = min(attack, defender_health)
            killed = dealt >= defender_health
            if getattr(attacker, "poisonous", 0) and dealt > 0:
                killed = True

        if getattr(attacker, "divine_shield", 0):
            taken = 0
        else:
            # 圣盾只挡伤害，不挡防守方的攻击力。
            taken = min(getattr(defender, "attack", 0) or 0, attacker_health)
        if (killed
                and getattr(defender, "card_id", None) in CARD_IDS_DOOMSAYER):
            # 本回合正好清掉末日预言者：它本来要清光我方场面，这一撞救全场
            # （用户 2026-10-25 标注 t006-s0）。
            gain += DOOMSAYER_KILL_BONUS
        if killed and Dwys._is_drain_minion(defender):
            # 敌方每回合持续吸血/偷血的随从（饱胀水蛭等）必须解掉：留着它
            # 每回合都在赚（用户 2026-10-25 标注 101941 t008-s1 / t010-s7）。
            gain += DRAIN_MINION_KILL_BONUS
        priority_kill_score = PRIORITY_KILL_SCORES.get(
            getattr(defender, "card_id", None))
        if priority_kill_score is not None:
            if killed:
                # 优先解列表：解掉按表给分（默认 15，火炮手 6）。
                gain += priority_kill_score
            elif dealt > 0:
                # 解不掉也要蹭血：按已造成伤害占目标生命的比例给分，方便
                # 本回合或下回合收掉（人类备注：哪怕解不掉，蹭血也是好的）。
                gain += PRIORITY_CHIP_BONUS * dealt / defender_health
        # 我方嘲讽随从主动换掉敌方低威胁的非嘲讽随从：对方本来就要来撞
        # 嘲讽，3 攻以下随从威胁不大，嘲讽留着打脸更赚（t008-s5）。
        # 例外：目标有持续收益（饱胀水蛭 EDR_810t 等）时本来就必须解掉，
        # 让嘲讽去换不算浪费（用户 2026-10-25 标注 101941 t008-s1：
        # 人类用 2/3 无面换掉 0/2 水蛭，把 4/4 腾出来打脸）。
        if (getattr(attacker, "taunt", 0)
                and not getattr(defender, "taunt", 0)
                and (getattr(defender, "attack", 0) or 0)
                <= TAUNT_LOW_THREAT_ATTACK
                and not (killed and Dwys._is_drain_minion(defender))):
            gain += TAUNT_LOW_THREAT_PENALTY
        # 留残血：自己活着、对方剩 1-2 血，下回合随便一只随从就能收掉，
        # 这种铺垫性的攻击值得做（用户 2026-10-24 标注 t007-s6：3/4 撞
        # 3/5 自己没死，未来和对方 4/2 交换也不亏）。只造成 1 点伤害的
        # 铺垫没有这个价值（125102 t008-s3）。
        defender_left = (0 if (killed or defender_shield)
                         else max(0, defender_health - dealt))
        if (dealt >= CHIP_SETUP_MIN_DAMAGE and not killed
                and taken < attacker_health
                and 0 < defender_left <= CHIP_SETUP_HEALTH):
            gain += CHIP_SETUP_BONUS
        # 亡语会打全体我方的敌方随从：主动攻击等于帮对方提前引爆亡语
        # （用户 2026-10-25 确认 125102 t012-s2，下水道小鬼不许打）。
        if getattr(defender, "card_id", None) in AOE_DEATHRATTLE_IDS:
            gain += AOE_DEATHRATTLE_ATTACK_PENALTY
        # 攻击力溢出同分打破：4 攻打 3 血目标会浪费 1 点攻击力，同分时优先
        # 让攻击力刚好够用的随从去换（t008-s3：先上 3/4 无面，4/3 牧师留着
        # 4 点攻击去打英雄或更大的随从）。攻击者本回合会阵亡时，溢出的攻击力
        # 没有任何未来价值，终局静态分已完整体现，不再扣（用户 2026-10-26）。
        if killed and taken < attacker_health:
            gain -= OVERKILL_TIEBREAK * max(0, attack - defender_health)
        return gain

    # ---------------------------------------------------------------- 工具
    @staticmethod
    def _choice_card_id(state: AiState, action: AiAction):
        """1 基选项号位 -> card_id；兼容 0 基/1 基/字符串三种键。"""
        options = getattr(state, "general_choice_cards", None) or {}
        index = getattr(action.source, "index", None)
        if not index:
            return None
        for key in (index - 1, index, str(index - 1), str(index)):
            value = options.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _target_entity(state: AiState, action: AiAction):
        target = action.target
        if target is None:
            return None
        if target.kind == "hero":
            if target.owner == "enemy":
                return getattr(state, "oppo_hero", None)
            return getattr(state, "my_hero", None)
        return Dwys._board_minion(state, target.owner or "enemy", target.index)

    @staticmethod
    def _board_minion(state: AiState, side: str, slot):
        if not slot:
            return None
        try:
            entries = ordered_board(state, side)
        except Exception:
            return None
        if not 1 <= slot <= len(entries):
            return None
        entry = entries[slot - 1]
        return entry.entity if entry.kind == "minion" else None

    @staticmethod
    def _vault_guard_neighbor_score(state, neighbor) -> float:
        """库区守卫贴住这只随从的收益：+2/+2 折现 + 大随从加权 + 解场加成。"""
        score = (VAULT_GUARD_BUFF_BASE
                 + VAULT_GUARD_BIG_MINION_BONUS
                 * Dwys._minion_value(neighbor, mine=True))
        attack = getattr(neighbor, "attack", 0) or 0
        for enemy in getattr(state, "oppo_minions", []) or []:
            health = getattr(enemy, "health", 0) or 0
            if attack < health <= attack + 2:   # +2 攻后正好能解掉这只
                score += VAULT_GUARD_TRADE_BONUS
                break
        return score

    @staticmethod
    def _vault_guard_best_gap(state, entries=None):
        """库区守卫的最佳落点：两侧都有随从的内侧空隙里相邻收益最高者。

        用户 2026-10-24：这张牌给左右两侧随从 +2/+2，一定不能放两侧边上。
        返回 (1 基空隙号, 相邻 buff 收益)；没有内侧空隙时返回 (None, 0)。
        """
        if entries is None:
            try:
                entries = ordered_board(state, "friendly")
            except Exception:
                return None, 0.0
        if len(entries) < 2:
            return None, 0.0
        best_gap, best_score = None, None
        for gap in range(2, len(entries) + 1):   # 空隙 1 与 len+1 是两侧边缘
            score = 0.0
            for entry in (entries[gap - 2], entries[gap - 1]):
                if entry.kind == "minion":
                    score += Dwys._vault_guard_neighbor_score(
                        state, entry.entity)
            if best_score is None or score > best_score:
                best_gap, best_score = gap, score
        return best_gap, float(best_score or 0.0)

    @staticmethod
    def _is_unhatched_egg(minion) -> bool:
        """凯洛斯的蛋系列未破壳（没有嘲讽）：打它只会加速孵化。"""
        return (getattr(minion, "card_id", None) in CARD_IDS_KELTHUZAD_EGG
                and not getattr(minion, "taunt", 0))

    @staticmethod
    def _minion_value(minion, mine: bool) -> float:
        """一个随从的价值：攻*2 + 血 + 关键字（用户 2026-10-26：不加铺场分）。

        手牌实体在搜索模拟里可能没有攻血/关键字标签，此时按 card_id 查人工表；
        已经在场上的实体一律以真实标签为准（圣盾被打掉后不能加回来）。
        """
        card_id = getattr(minion, "card_id", None)
        on_hand = str(getattr(minion, "zone", "") or "").upper() == "HAND"

        attack = getattr(minion, "attack", 0) or 0
        health = getattr(minion, "health", 0) or 0
        if on_hand and health <= 0:
            attack, health = item_stats(card_id)

        # 风怒随从一回合能打两下：价值按攻击力翻倍算（用户 2026-10-30）。
        windfury = bool(getattr(minion, "windfury", 0))
        if windfury:
            attack *= 2
        value = attack * ATTACK_WEIGHT + health * HEALTH_WEIGHT
        for flag, weight in (("taunt", TAUNT_VALUE),
                             ("reborn", REBORN_VALUE),
                             ("divine_shield", DIVINE_SHIELD_VALUE),
                             ("rush", RUSH_VALUE),
                             ("life_steal", LIFESTEAL_VALUE),
                             ("poisonous", POISONOUS_VALUE),
                             ("stealth", STEALTH_VALUE)):
            if getattr(minion, flag, 0):
                value += weight
            elif on_hand:
                value += item_flag(card_id, flag) * weight
        return value

    @staticmethod
    def _enemy_lethal_next_turn(state: AiState) -> bool:
        """对手下回合可见场攻 >= 我方英雄生命：延迟牌救不了场。"""
        health = getattr(state.my_hero, "health", 30) or 0
        if health <= 0:
            return False
        return Dwys._enemy_next_turn_damage(state) >= health

    @staticmethod
    def _must_clear_now(state: AiState) -> bool:
        """场面压力到必须立刻清场（用户 2026-10-25 标注 t008-s0）。

        人类口径：对方场攻 + 随从数量 >= 10，并且挨完这一轮场攻后我方
        英雄血量会掉到 <= 15，就必须当回合解场，不能出延迟收益的牌。
        打不到脸的场攻不算（嘲讽会先吸收，见 _enemy_face_damage_next_turn）。
        """
        health = getattr(state.my_hero, "health", 30) or 0
        if health <= 0:
            return False
        attack_sum = sum(
            max(0, getattr(minion, "attack", 0) or 0)
            for minion in getattr(state, "oppo_minions", []) or []
            if not (getattr(minion, "frozen", 0)
                    or getattr(minion, "dormant", 0)
                    or getattr(minion, "cant_attack", 0)))
        count = len(getattr(state, "oppo_minions", []) or ())
        if attack_sum + count < CLEAR_PRESSURE_SUM:
            return False
        return health - attack_sum <= CLEAR_PRESSURE_HEALTH

    @staticmethod
    def _enemy_face_damage_next_turn(state: AiState) -> float:
        """敌方下回合真正能打到我方英雄的伤害：先扣掉嘲讽随从的吸收。

        血量与斩杀判断要看“脸伤”而不是“总场攻”：两个大嘲讽在场时，
        对方场攻会先去撞墙（用户 2026-10-25 确认 t015-s0：13 点场攻被
        7/6、7/8 两个嘲讽全部吸收，可以安全分流）。
        """
        total = Dwys._enemy_next_turn_damage(state)
        taunt_health = sum(
            max(0, getattr(minion, "health", 0) or 0)
            for minion in getattr(state, "my_minions", []) or []
            if getattr(minion, "taunt", 0))
        return max(0.0, total - taunt_health)

    @staticmethod
    def _doomed_by_enemy_board(state: AiState, minion) -> bool:
        """我方这只随从下回合本来就会被敌方白吃（通用交手框架）。

        敌方至少有一只下回合能出手（未冻结/休眠/无法攻击）的随从，攻击力
        不低于这只随从的当前生命。圣盾随从吃第一下不会死，不算。
        """
        if getattr(minion, "divine_shield", 0):
            return False
        health = max(0, getattr(minion, "health", 0) or 0)
        if health <= 0:
            return False
        for enemy in getattr(state, "oppo_minions", []) or []:
            if (getattr(enemy, "frozen", 0) or getattr(enemy, "dormant", 0)
                    or getattr(enemy, "cant_attack", 0)):
                continue
            if max(0, getattr(enemy, "attack", 0) or 0) >= health:
                return True
        return False

    @staticmethod
    def _is_drain_minion(minion) -> bool:
        """敌方会“每回合持续吸血/偷血”的随从。

        只认持续生效的偷血牌（饱胀水蛭等）。普通随从的 life_steal 只在它
        自己攻击时回血，是一次性效果，doAction 能模拟，击杀收益已由终局
        静态分体现，不能再额外加击杀分（用户 2026-10-26）。
        """
        return getattr(minion, "card_id", None) in DRAIN_MINION_CARD_IDS

    @staticmethod
    def _doomsayer_unkillable(state: AiState) -> bool:
        """敌方末日预言者本回合杀不掉（用户 2026-10-25 标注 t006-s0）。

        末日预言者在对手回合开始时消灭所有随从。杀不掉时，这回合出的随从、
        给随从加的属性都会白费，所以出牌评分统一扣 DOOMSAYER_WASTE_PENALTY。

        “能不能杀掉”只看本回合能出手的随从攻击力（exhausted / frozen /
        dormant / cant_attack 的随从打不出伤害），与它们的血量取剩余值。
        """
        targets = [
            minion for minion in getattr(state, "oppo_minions", []) or []
            if getattr(minion, "card_id", None) in CARD_IDS_DOOMSAYER
        ]
        if not targets:
            return False
        needed = sum(max(0, getattr(minion, "health", 0) or 0)
                     for minion in targets)
        available = sum(
            max(0, getattr(minion, "attack", 0) or 0)
            for minion in getattr(state, "my_minions", []) or []
            if not (getattr(minion, "exhausted", 0)
                    or getattr(minion, "frozen", 0)
                    or getattr(minion, "dormant", 0)
                    or getattr(minion, "cant_attack", 0)))
        return available < needed

    @staticmethod
    def _frozen_by_earlier(actions: list[AiAction], action: AiAction) -> bool:
        """同一条序列里前面已经用冰川裂片冻过敌方英雄：再冻一次什么都不阻止。

        只比对英雄：随从号位会随出牌/死亡变化，用号位判断“同一只随从”不可靠，
        而且冻结随从的收益本来就很小（按攻击力折现）。
        """
        target = getattr(action, "target", None)
        if target is None or target.kind != "hero":
            return False
        for earlier in list(actions or ())[:-1]:
            if getattr(earlier, "card_id", None) not in (
                    CARD_ID_GLACIER, CARD_ID_GLACIER_LEGACY):
                continue
            other = getattr(earlier, "target", None)
            if (other is not None and other.kind == "hero"
                    and (other.owner or "enemy") == (target.owner or "enemy")):
                return True
        return False

    @staticmethod
    def _glacier_freeze_gain(state: AiState, action: AiAction,
                            actions: list[AiAction] = ()) -> float:
        """冰川裂片战吼：冻结一个敌人，按“阻止的伤害”折现。"""
        target = getattr(action, "target", None)
        if target is None:
            return 0.0
        if Dwys._frozen_by_earlier(actions, action):
            # 同一条序列里已经冻过这个目标：第二次冻结什么都不阻止。
            return 0.0
        if target.kind == "hero":
            hero = getattr(state, "oppo_hero", None)
            if hero is None or getattr(hero, "frozen", 0):
                return 0.0
            weapon = getattr(state, "oppo_weapon", None)
            score = WEAPON_FREEZE_SCORES.get(
                getattr(weapon, "card_id", None) if weapon is not None
                else None, 0.0)
            if score:
                # 敌方英雄挂着关键武器：冻住他等于让武器这一回合打不出来。
                return score
            return GLACIER_FREEZE_HERO
        minion = Dwys._target_entity(state, action)
        if minion is None or getattr(minion, "frozen", 0):
            return 0.0
        attack = max(0, getattr(minion, "attack", 0) or 0)
        if attack <= 0:
            return 0.0
        gain = min(GLACIER_FREEZE_CAP, GLACIER_FREEZE_PER_ATTACK * attack)
        health = max(0, getattr(state.my_hero, "health", 0) or 0)
        damage = Dwys._enemy_next_turn_damage(state)
        if health > 0 and damage >= health and damage - attack < health:
            # 冻住这只后敌方下回合伤害不足斩杀：这一冻正好救命。
            gain += GLACIER_PREVENT_LETHAL
        # 我方下回合场攻已经足以斩杀：冻住敌方最大攻击随从，保住自己的场面
        # 不被白换（111710 t013-s3 人类备注：冻一个敌方随从保护自己随从，
        # 争取下回合斩杀）。只在目标就是敌方最大攻击随从时给一次大分。
        face = sum(
            max(0, getattr(minion, "attack", 0) or 0)
            for minion in getattr(state, "my_minions", []) or []
            if not (getattr(minion, "frozen", 0)
                    or getattr(minion, "dormant", 0)))
        hero_health = getattr(
            getattr(state, "oppo_hero", None), "health", 0) or 0
        biggest = max(
            (max(0, getattr(enemy, "attack", 0) or 0)
             for enemy in getattr(state, "oppo_minions", []) or []
             if not (getattr(enemy, "frozen", 0)
                     or getattr(enemy, "dormant", 0)
                     or getattr(enemy, "cant_attack", 0))),
            default=0)
        if hero_health > 0 and face >= hero_health and attack >= biggest:
            gain += GLACIER_PROTECT_LETHAL
        return gain

    @staticmethod
    def _enemy_next_turn_damage(state: AiState) -> float:
        total = 0.0
        for minion in getattr(state, "oppo_minions", []) or []:
            if (getattr(minion, "frozen", 0) or getattr(minion, "dormant", 0)
                    or getattr(minion, "cant_attack", 0)):
                continue
            total += max(0, getattr(minion, "attack", 0) or 0)
        hero = getattr(state, "oppo_hero", None)
        if hero is not None and not getattr(hero, "frozen", 0):
            total += max(0, getattr(hero, "attack", 0) or 0)
        return total

    @staticmethod
    def _my_face_damage(state: AiState) -> float:
        total = 0.0
        for minion in getattr(state, "my_minions", []) or []:
            if getattr(minion, "can_beat_face", False):
                total += max(0, getattr(minion, "attack", 0) or 0)
        hero = getattr(state, "my_hero", None)
        if hero is not None and getattr(hero, "can_attack", False):
            total += max(0, getattr(hero, "attack", 0) or 0)
        return total

    @staticmethod
    def _played_minion_before(actions: list[AiAction]) -> bool:
        return any(
            action.action == ActionKind.PLAY_CARD
            and action.card_type == "MINION"
            for action in actions[:-1])

    @staticmethod
    def _minions_played_in(actions) -> int:
        """序列里打出的随从数量（用来还原某个早前动作发生时的场面）。"""
        return sum(
            1 for action in (actions or ())
            if action.action == ActionKind.PLAY_CARD
            and action.card_type == "MINION")

    @staticmethod
    def _hand_minion_count(state: AiState, exclude=None) -> int:
        return sum(
            1 for card in getattr(state, "my_hand_cards", []) or []
            if getattr(card, "cardtype", None) == "MINION"
            and not Dwys._same_card(card, exclude))

    @staticmethod
    def _hand_spell_count(state: AiState) -> int:
        return sum(
            1 for card in getattr(state, "my_hand_cards", []) or []
            if getattr(card, "cardtype", None) == "SPELL")

    @staticmethod
    def _hand_has(state: AiState, card_id) -> bool:
        return any(getattr(card, "card_id", None) == card_id
                   for card in getattr(state, "my_hand_cards", []) or [])

    @staticmethod
    def _zakaz_unlocks_kishix(state: AiState) -> bool:
        """扎卡兹的随从减费是否正好让基希克斯提前一回合上场。

        条件：手里有基希克斯 BE_090、本局已完成仪式，且减 1 费后“下回合的
        法力”刚好够出它（下一回合法力 = 本回合法力上限 + 1）。例：4 费回合
        打出扎卡兹，基希克斯 6 费变 5 费，下回合 5 费正好能出
        （111710 t007-s1 人类备注）。
        """
        if not Dwys._hand_has(state, CARD_ID_KISHIX):
            return False
        if not Dwys._context(state).ritual_completed:
            return False
        cost = int(card_item(CARD_ID_KISHIX).get("cost", 0) or 0)
        if cost <= 0:
            return False
        mana_next = min(10, int(getattr(state, "my_total_mana", 0) or 0) + 1)
        return cost - 1 <= mana_next < cost

    @staticmethod
    def _is_playable_now(state: AiState, card, remaining: int,
                         board_count: int) -> bool:
        """这张手牌在本回合能否打出：费用、7 格上限、指向性法术有目标。"""
        cost = Dwys._card_cost(card)
        if cost < 0 or cost > remaining:
            return False
        if (getattr(card, "cardtype", None) in ("MINION", "LOCATION")
                and board_count >= 7):
            return False
        return not (Dwys.requires_target(card)
                    and not Dwys.possible_targets(card, state))

    @staticmethod
    def _has_playable_card(state: AiState, card_id, *, remaining=None,
                           board_count=None) -> bool:
        """手里是否有指定 card_id、本回合付得起且出得去的一张。

        remaining / board_count 可传“先出一张别的牌之后”的法力与场面格数。
        """
        if remaining is None:
            remaining = getattr(state, "my_last_mana", 0) or 0
        remaining = max(0, remaining)
        if board_count is None:
            board_count = getattr(state, "my_board_slot_num", 0) or 0
        for other in getattr(state, "my_hand_cards", []) or []:
            if getattr(other, "card_id", None) != card_id:
                continue
            if Dwys._is_playable_now(state, other, remaining, board_count):
                return True
        return False

    @staticmethod
    def _has_strong_playable_card(state: AiState, later=()) -> bool:
        """手里是否有 3 费及以上、本回合付得起且出得去的牌。

        咒怨之墓 TLC_451 的判断依据（用户 2026-10-23）：手牌有像样的牌时
        先把牌打了，没有好选择或想找特定的牌时才用地标发现临时牌。

        later 是序列里排在咒怨之墓之后的动作：评分拿到的 state 是序列末尾
        的场面，后面的出牌已经把法力花掉、牌也离开了手牌，必须加回来才是
        “打出咒怨之墓那一刻”的手牌与法力（标注 t006-s0）。
        """
        remaining = max(0, getattr(state, "my_last_mana", 0) or 0)
        board_count = getattr(state, "my_board_slot_num", 0) or 0
        cards = list(getattr(state, "my_hand_cards", []) or [])
        for action in later or ():
            if action.action != ActionKind.PLAY_CARD:
                continue
            remaining += max(0, getattr(action, "cost", 0) or 0)
            if action.card_type == "MINION":
                board_count -= 1
            card = getattr(action, "card", None)
            if card is not None:
                cards.append(card)
        for other in cards:
            card_id = getattr(other, "card_id", None)
            if card_id in (CARD_ID_CRYPT, CARD_ID_RITUALIST):
                continue
            if Dwys._card_cost(other) < COIN_ENABLED_MIN_COST:
                continue
            if Dwys._is_playable_now(state, other, remaining, board_count):
                return True
        return False

    @staticmethod
    def _mana_at_play(state: AiState, action: AiAction,
                      later: list[AiAction] = ()) -> int:
        """还原“打出这张牌那一刻”的剩余法力（已扣掉本动作费用）。

        later 是序列里排在它之后的**全部**动作，但评分拿到的 state 是“序列
        最后一个动作还没模拟”的场面：最后一个动作的费用还没扣、牌也还在手里，
        所以只能把 later[:-1] 已扣的费用加回来，最后一个不能加（否则会把本来
        付不起的牌算成付得起）。action 自己就是最后一个动作时，它的费用也还
        没扣，要在这里减一次。
        """
        remaining = max(0, getattr(state, "my_last_mana", 0) or 0)
        later = list(later or ())
        if not later:
            return remaining - max(0, getattr(action, "cost", 0) or 0)
        for item in later[:-1]:
            if item.action == ActionKind.PLAY_CARD:
                remaining += max(0, getattr(item, "cost", 0) or 0)
        return remaining

    @staticmethod
    def _same_card(first, second) -> bool:
        if first is None or second is None:
            return False
        if first is second:
            return True
        first_id = getattr(first, "entity_id", None)
        second_id = getattr(second, "entity_id", None)
        return bool(first_id) and first_id == second_id

    @staticmethod
    def _card_cost(card) -> int:
        try:
            return int(getattr(card, "current_cost", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _is_coin(card_id) -> bool:
        return card_id == "GAME_005" or "COIN" in str(card_id or "")

    @staticmethod
    def _first_play(actions) -> bool:
        """本动作之前还没有别的“出牌”动作（硬币只是跳费，不算抢时机）。

        与 _key_card_order_penalty 的口径一致：
        幸运币在评分里只是凑费手段，把它算成“已经出过牌”会让先跳币的
        正确顺序被误判成“不是首出”（用户 2026-10-24 标注 t002-s0）。
        """
        for action in list(actions or ())[:-1]:
            if (action.action == ActionKind.PLAY_CARD
                    and not Dwys._is_coin(getattr(action, "card_id", None))):
                return False
        return True
