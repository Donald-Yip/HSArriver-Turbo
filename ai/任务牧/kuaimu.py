# 快任务牧（kuaimu）策略卡组
#
# 打法依据是同目录的 ai/kuaimu.txt（留牌策略 / 打法思路 / 单卡注意事项）。
# 评分框架与 renwumu 一致：
#   rankState(state, actions) = rankStaticState(state) + rankActions(state, actions)
#   * rankStaticState —— 只看场面：随从攻血、嘲讽/复生、英雄血量、任务是否完成；
#   * rankActions     —— 只看"最新那个动作"的增量（actions[-1]）。
# 搜索里每次算分用的是"动作发生前的旧场面 + 新动作的增量"（见 search.aiSearch
# 的注释），所以这里的增量必须自己估算出动作的效果。
#
# 认牌约定（用户要求）：一律用 card_id，卡名只写在常量/注释里。
# 新版本 4 张新卡不在旧 cards.json 里，ID 来自官方最新卡表：
#   BE_223 古言术：笞 / BE_220 熵能裂隙 / BE_221 古言术：预 / BE_228 祖拉玛特的仆从
#
# 本卡组的制胜点：
#   1. 1～3费刷满两条任务（各4个神圣/暗影法术），5～6费拍循环新生
#      （TLC_817t5，5费8/8，嘲讽+复生+战吼复制+亡语5伤）；
#   2. 幽魂不散/捉鬼专家给循环新生加复生/复制，滚雪球；
#   3. 灵质处决清场吃循环新生亡语，麦迪文的胜利控场，雷斯复活收尾。
import re

from src.recommendation_models import ActionKind
from search.ai_action import AiAction, AiSlot
from search.ai_state import AiState
from src.game_state.recommendation_adapter import ordered_board

from .kuaimu_context import (
    KuaimuDeckContext,
    card_item,
    card_text,
    spell_school,
    CARD_ID_QUEST,
    CARD_ID_SOULTRONUS,
    CARD_ID_LIFE_HALF,
    CARD_ID_DEATH_HALF,
)


class Kuaimu:
    # ================================================================ 参数区
    # ---------------------------------------------------------------- 卡牌 ID
    # 括号里是中文名，只作注释；所有判断都用 card_id。
    CARD_ID_FLAY = "BE_223"              # 古言术：笞
    CARD_ID_MEND = "CATA_302"            # 愈合
    CARD_IDS_SHIELD = frozenset({        # 真言术：盾（标准/经典/幻变多个版本）
        "CORE_CS2_004", "CS2_004", "CS2_004_Puzzle", "VAN_CS2_004"})
    CARD_ID_ENTROPIC = "BE_220"          # 熵能裂隙
    CARD_ID_SERVANT = "BE_228"           # 祖拉玛特的仆从
    CARD_ID_TWILIGHT = "EDR_463"         # 暮光侵扰
    CARD_ID_TWILIGHT_DESTROY = "EDR_463a"  # 操纵荆棘（消灭≤3攻随从）
    CARD_ID_TWILIGHT_SUMMON = "EDR_463b"   # 控制藤蔓（随机召唤2费随从）
    CARD_ID_LIFE_RITUAL = "DINO_426"     # 生命仪式
    CARD_ID_HOLY_EMBRACE = "JAIL_941"    # 神圣之拥
    CARD_ID_DARK_EMBRACE = "JAIL_941t"   # 黑暗之拥（神圣之拥衍生）
    CARD_ID_FORESIGHT = "BE_221"         # 古言术：预
    CARD_ID_NOW = "BE_222"               # 古言术：现
    CARD_ID_GHOST_GRIP = "CAP_801"       # 幽魂不散
    CARD_ID_ERASE = "TIME_433"           # 抹除存在
    CARD_ID_GHOSTHUNTER = "CAP_804"      # 捉鬼专家
    CARD_ID_SUNFLOWER = "TLC_816"        # 墓晨太阳花
    CARD_ID_EXECUTE = "CAP_805"          # 灵质处决
    CARD_ID_MEDIVH = "CATA_308"          # 麦迪文的胜利
    CARD_ID_RAGS = "CAP_806"             # 雷斯·范盖斯特

    # ---------------------------------------------------------------- 留牌
    # 必留：任务、笞、生命仪式、暮光侵扰（2费曲线）
    # 祖拉玛特的仆从/神圣之拥/墓晨太阳花：永远不留。
    MULLIGAN_ALWAYS_KEEP = frozenset({
        CARD_ID_QUEST, CARD_ID_FLAY,
        CARD_ID_LIFE_RITUAL, CARD_ID_TWILIGHT,
    })
    # 慢速/中速才留：预、熵能裂隙（对快攻太慢）
    MULLIGAN_SLOW_KEEP = frozenset({
        CARD_ID_FORESIGHT, CARD_ID_ENTROPIC,
    })
    # 对快攻才留：麦迪文的胜利（5费全场打4）
    MULLIGAN_AGGRO_KEEP = frozenset({CARD_ID_MEDIVH})
    # 判断对手是否快攻用的职业集合（按英雄卡 cardClass；认不出时按前缀）。
    AGGRO_CLASSES = frozenset({
        "HUNTER", "WARRIOR", "ROGUE", "DEMONHUNTER", "WARLOCK"})
    HERO_CLASS_BY_PREFIX = {
        "HERO_01": "WARRIOR", "HERO_02": "SHAMAN", "HERO_03": "ROGUE",
        "HERO_04": "PALADIN", "HERO_05": "HUNTER", "HERO_06": "DRUID",
        "HERO_07": "WARLOCK", "HERO_08": "MAGE", "HERO_09": "PRIEST",
        "HERO_10": "DEMONHUNTER", "HERO_11": "DEATHKNIGHT",
    }

    # ---------------------------------------------------------------- 场面评分
    ATTACK_WEIGHT = 2          # 1 点攻击 = 2 分
    HEALTH_WEIGHT = 1          # 1 点血 = 1 分
    MY_HERO_WEIGHT = 1         # 我方英雄 1 点血 = 1 分
    ENEMY_HERO_WEIGHT = 1      # 敌方英雄 1 点血 = 1 分
    BOARD_PRESENCE_SCORE = 3   # 我方每多一个随从的铺场分
    TAUNT_VALUE = 1            # 嘲讽价值
    REBORN_VALUE = 2           # 复生价值
    DIVINE_SHIELD_VALUE = 2    # 圣盾价值
    LETHAL_BONUS = 1000.0      # 能直接打死对手的天价分

    # ---------------------------------------------------------------- 任务
    QUEST_FIRST_TURN_BONUS = 100.0  # 第一回合必下任务
    QUEST_LATER_BONUS = 8.0         # 错过第一回合后补拍
    QUEST_CAST_BONUS = 6.0          # 对应学派任务没完成时，施放一做法术的进度分
    QUEST_COMPLETE_BONUS = 20.0     # 这一手刚好完成最后一边任务
    QUESTS_DONE_STATIC_BONUS = 5.0  # 双任务都完成后场面分加成

    # ---------------------------------------------------------------- 单卡效果分
    FLAY_DAMAGE = 3                 # 古言术：笞随机3伤
    FLAY_SETUP_BONUS = 2.0          # 手里还有暗影法术可接
    FORESIGHT_DRAW_VALUE = 3.0      # 预抽1
    FORESIGHT_DISCOUNT_VALUE = 2.0  # 预减2费
    FORESIGHT_REPEAT_BONUS = 3.0    # 重复预的抽牌/减费
    FORESIGHT_SETUP_BONUS = 2.0     # 手里还有暗影法术可接
    ENTROPIC_BASE = 4.0             # 熵能裂隙：两张随机暗影牌的期望价值
    ENTROPIC_HAND_FULL_PENALTY = 10.0
    SHIELD_DRAW_VALUE = 2.0         # 真言术：盾抽1
    SHIELD_HEALTH_VALUE = 2.0       # +2生命
    MEND_DRAW_VALUE = 2.0           # 愈合抽1
    LIFE_RITUAL_COPY_VALUE = 7.0    # 生命仪式的2/3复制
    LIFE_RITUAL_DISCOVERED_VALUE = 5.0  # 发现的3费随从
    LIFE_RITUAL_NO_SPACE_PENALTY = 12.0
    HOLY_EMBRACE_BASE = 3.0         # 神圣之拥基础
    HOLY_EMBRACE_HEAL = 4           # 神圣之拥恢复4
    HOLY_EMBRACE_FUEL_BONUS = 2.0   # 送的黑暗之拥是暗影任务燃料
    GHOST_GRIP_STATS = 7.0          # +2攻(4) +3血(3)
    GHOST_GRIP_REBORN = 3.0
    GHOST_GRIP_TAUNT = 1.0
    GHOST_GRIP_KEY_BONUS = 3.0      # 贴给循环新生
    GHOST_GRIP_SETUP_BONUS = 2.0    # 手里有捉鬼专家可接
    GHOST_GRIP_SMALL_TARGET_PENALTY = 3.0  # 贴1/1这种站不住的随从
    GHOSTHUNTER_REBORN_GRANT = 4.0  # 给普通随从贴复生
    GHOSTHUNTER_VALUE_FACTOR = 0.5  # 目标越值越优先（非循环新生时按随从价值加成）
    GHOSTHUNTER_KEY_BONUS = 30     # 复制循环新生
    GHOSTHUNTER_SMALL_TARGET_PENALTY = 2.0  # 给1/1贴复生收益低
    GHOSTHUNTER_WASTE_PENALTY = 50.0  # 先捉鬼再幽魂（同一目标）复生白给
    SERVANT_TAUNT_SILENCE = 4.0
    SERVANT_REBORN_SILENCE = 5.0
    SERVANT_DEATHRATTLE_SILENCE = 4.0
    SERVANT_DIVINE_SHIELD_SILENCE = 3.0
    SERVANT_BIG_THREAT = 3.0
    #TWILIGHT_DESTROY_TEMPO_BONUS = 3.0  # 消灭=去掉一个敌方场面，比纯数值多值一点
    TWILIGHT_DESTROY_DEATHRATTLE_PENALTY = 2.0
    TWILIGHT_SUMMON_VALUE = 9.0      # 随机2费随从约2/2 + 铺场分
    TWILIGHT_SUMMON_NO_SPACE_PENALTY = 20.0
    ERASE_NO_TARGET_PENALTY = 1000.0  # 敌方空场时抹除存在没有目标
    ERASE_WIDE_BOARD_PENALTY = 2.0    # 随机单解在宽场面上的方差惩罚
    SUNFLOWER_DRAW_VALUE = 4.0       # 抽2
    SUNFLOWER_KINDRED_BONUS = 4.0    # 延系后只要2费
    SUNFLOWER_HAND_FULL_PENALTY = 5.0
    EXECUTE_NO_SOULTRONUS_PENALTY = 10.0
    EXECUTE_DEATHRATTLE_VALUE = 5.0  # 循环新生亡语随机5伤
    EXECUTE_REBORN_VALUE = 8.0       # 循环新生复生后1血8/8嘲讽
    EXECUTE_RESET_FACTOR = 0.5       # 纯清场的收益折扣（对方也会拿返场法术）
    EXECUTE_OPPONENT_RESUMMON_FACTOR = 0.4
    MEDIVH_DAMAGE = 4                # 麦迪文的胜利打4
    MEDIVH_CHEAP_BONUS = 8.0         # 雷斯站场时1费
    MEDIVH_NO_ENEMY_PENALTY = 10.0   # 敌方空场时不要空放（只会伤自己）
    MEDIVH_NEGATIVE_EFFECT_MULTIPLIER = 3.0  # 净伤自己场面时重罚，避免为任务空打
    RAGS_BODY = 18.0                 # 雷斯5/5 + 铺场分（_minion_value 已含）
    RAGS_PER_REBORN = 18.0           # 每复活一个复生随从
    RAGS_SOULTRONUS_BONUS = 8.0      # 复活池里有循环新生
    RAGS_NO_POOL_PENALTY = 20.0      # 没有复生死亡/满格
    SOULTRONUS_COPY_VALUE = 33.0     # 战吼复制另一个8/8（含嘲讽/复生）
    SOULTRONUS_KEYWORD_BONUS = 6.0   # 本体嘲讽/复生/亡语
    HALF_HOLD_PENALTY = 200.0          # 单出半身：留着等合体
    HALF_COMBINE_WAIT_PENALTY = 50.0 # 另一张半身已在手：等自动合体
    HALF_QUEST_UNFINISHED_PENALTY = 500.0  # 任务没做完就单出半身：放弃合体
    COIN_ENABLER_BONUS = 2.0         # 硬币刚好点亮一张牌
    COIN_WASTE_PENALTY = 3.0         # 白出硬币：留着
    DISCOVER_DOMINATE_BASE = 50.0    # 发现界面必须选一个
    DISCOVER_REBORN_BONUS = 8.0      # 生命仪式发现：优先复生
    DISCOVER_TAUNT_BONUS = 5.0
    DISCOVER_BATTLECRY_BONUS = 4.0
    DISCOVER_DEATHRATTLE_BONUS = 2.0
    DISCOVER_STAT_FACTOR = 0.2

    # ---------------------------------------------------------------- 随机暗影法术
    # 熵能裂隙（BE_220）在标准环境能随机出的 76 张暗影法术（清单见同目录
    # kuaimu_anying.txt）。评分按 card id 查表、按当前场面动态估值；不在表里的
    # 暗影法术走卡面文本兜底（_generic_shadow_gain）。
    #
    # 表格式：card_id: (目标模式, 效果字典)
    #   目标模式（给 search._possible_targets / _requires_target 用）：
    #     None 无目标；any_minion 双方随从；enemy_minion 敌方随从；
    #     friendly_minion 己方随从；friendly_minion_damaged 受伤的己方随从；
    #     enemy_any 敌方随从+敌方英雄；friendly_any 己方随从+己方英雄；
    #     any 双方随从+双方英雄。
    #   效果字典里的数值都是"按场面估算"的原始值，由 _shadow_effects_gain 折算。
    SHADOW_DRAW_VALUE = 2.5          # 抽1张
    SHADOW_DISCOVER_SPELL = 3.0      # 发现1张法术
    SHADOW_DISCOVER_MINION = 5.0     # 发现1张随从
    SHADOW_NO_SPACE_PENALTY = 6.0    # 己方满场还召唤
    SHADOW_HAND_FULL_PENALTY = 3.0   # 手牌快满时抽牌
    SHADOW_UNSUPPORTED_PENALTY = 100.0  # 无法安全执行的牌（克苏恩技能菜单）
    SHADOW_WING_SUMMON_VALUE = 16.0  # 古言术：现 的5/5吸血被下一张暗影法术重复
    SHADOW_KEYWORD_VALUES = {
        "taunt": 1.0, "reborn": 2.0, "divine_shield": 2.0, "rush": 2.0,
        "lifesteal": 2.0, "poisonous": 3.0, "stealth": 1.0,
        "elusive": 1.0, "cantattack": -2.0,
    }

    RANDOM_SHADOW_CARDS = {
        # ---------------------------------------------------------- 死亡骑士
        "DINO_417": (None, {"buff_all": (1, 0, ("rush",)),
                            "penalty": 3.0}),          # 安魂仪典
        "EDR_811": (None, {"discover_minion": 1}),     # 暴行祭礼
        "BE_325": ("enemy_minion", {"destroy": True,
                                    "enemy_heal": True}),  # 凶煞降临
        "CORE_RLK_712": (None, {"hand_buff": (1, 1)}), # 活力分流
        "JAIL_441": ("enemy_minion", {"dmg": 3, "lifesteal": 3,
                                      "resource": 1.0}),  # 饮血术
        "BE_571": (None, {"assemble": 5.0,
                          "hand_buff": (2, 2)}),       # 血肉塑造
        "CORE_RLK_087": (None, {"destroy_highest": True}),  # 窒息
        "JAIL_451": (None, {"discover_minion": 1}),    # 鲜血克隆
        "RLK_048": (None, {"buff_all": (1, 1, ("elusive",))}),  # 反魔法护罩
        "TIME_612": (None, {"discover_spell": 1}),     # 赤红汲取
        "TLC_434": (None, {"discover_minion": 1}),     # 古生物秘术
        "CORE_RLK_118": (None, {"summon": (2, 2, 2, ("taunt",))}),  # 坟墓守卫
        "RLK_707": (None, {"buff_all": (1, 0, ())}),   # 墓地之力
        "TIME_616": (None, {"resource": 6.0}),         # 悼念成真
        "BE_EVENT_100": (None, {"draw_minions": 2,
                                "refresh_mana": 5}),   # 古神降临
        "RLK_060": (None, {"summon": (2, 2, 2, ("rush",))}),  # 亡者大军
        "CATA_156": (None, {"aoe_enemy": 4,
                            "resource": 2.0}),         # 试验演示
        "TIME_610": (None, {"summon": (4, 3, 2, ()),
                            "resource": 6.0}),         # 昨日之影
        # ---------------------------------------------------------- 恶魔猎手
        "JAIL_732": (None, {"summon_random": (1, 1),
                            "resource": 1.0}),         # 虚空灵魂
        "JAIL_206": (None, {"draw": 2, "resource": 1.0}),  # 黑暗贿赂
        "JAIL_891": ("enemy_minion", {"dmg": 3,
                                      "resource": 1.0}),  # 虚空冲击
        "JAIL_892": ("enemy_any", {"dmg": 2, "resource": 0.5}),  # 宇宙具象
        "JAIL_EVENT_101": (None, {"resource": 2.0}),   # 灵魂献祭
        "BE_873": (None, {"delay": 0.7,
                          "summon": (3, 3, 1, ("rush",))}),  # 孵化咒符
        # ---------------------------------------------------------- 牧师
        # 笞/预/暮光侵扰/幽魂不散/灵质处决已在 _spell_gain 单独建模，不重复收录。
        "JAIL_940": (None, {"resource": 2.0}),         # 不死徒刑
        "CAP_802": (None, {"summon": (1, 2, 1, ("reborn",)),
                           "resource": 1.5}),          # 跟随幽灵
        "CORE_BAR_311": (None, {"dmg_split_minions": 4,
                                "lifesteal": 4}),      # 噬灵疫病
        "CATA_306": ("friendly_minion",
                     {"buff_target": (2, 3, ("elusive",)),
                      "copy_target": True}),           # 教派分歧（裂变合体）
        # 裂变牌进手牌会拆成两半，随机拿到的是子卡，两个子卡各算一半效果。
        "CATA_306t1": ("friendly_minion",
                       {"buff_target": (2, 3, ("elusive",))}),  # 教派分歧（已裂变·增益）
        "CATA_306t2": ("friendly_minion",
                       {"copy_target": True}),         # 教派分歧（已裂变·复制）
        "CORE_EX1_197": (None, {"destroy_all_attack_ge": 5}),  # 暗言术：毁
        "CORE_SW_442": ("enemy_any", {"dmg": 4,
                                      "lifesteal": 4}),  # 虚空碎片
        "TLC_815": (None, {"summon_random": (4, 1),
                           "taunt": True}),            # 墓晨虚空芽
        "BE_222": (None, {"summon": (1, 5, 5, ("lifesteal",)),
                          "setup": 8.0}),              # 古言术：现
        "BE_EVENT_102": ("enemy_minion", {"dmg": 5,
                                          "splash": 5}),  # 裂地黑暗
        "JAIL_433": ("enemy_minion", {"destroy": True}),  # 无拘之魂
        "CORE_SCH_512": ("enemy_minion", {"dmg": 4,
                                          "copy_on_kill": True}),  # 通窍
        "TLC_818": (None, {"resource": 12.0}),         # 轮回转生
        # ---------------------------------------------------------- 潜行者
        "BE_071": (None, {"aoe_enemies": 1, "penalty": 1.0}),  # 暗影之舞
        "CORE_RLK_567": (None, {"resource": 1.0}),     # 殒命暗影
        "BE_724": (None, {"resource": 4.5}),           # 孵化
        "CAP_001": ("friendly_minion",
                    {"buff_target": (3, 0, ())}),      # 静默打击
        "CATA_785": ("enemy_any", {"resource": 2.0,
                                   "dmg": 3}),         # 暮光祭礼（连击）
        "END_000": ("enemy_any", {"dmg": 2,
                                  "resource": 0.5}),   # 不测之变
        "TLC_515": (None, {"resource": 3.0}),          # 异教地图
        "CATA_202": (None, {"resource": 3.0}),         # 能量窃取
        "CATA_215": ("enemy_minion", {"bounce": True}),  # 眩晕
        "TLC_519": (None, {"summon": (1, 1, 1,
                                      ("stealth", "poisonous"))}),  # 潜踪掠食
        "TIME_712": ("enemy_minion", {"destroy": True,
                                      "resource": 2.0}),  # 诛灭暴君
        # ---------------------------------------------------------- 萨满祭司
        "BE_114": (None, {"assemble": 6.0}),           # 暗影塑形
        # 选择并触发克苏恩技能会弹选择菜单，执行层无法安全处理：宁可不打。
        "BE_778": (None, {"unsupported": True}),       # 凌空守望
        # ---------------------------------------------------------- 术士
        "BE_094": ("friendly_minion",
                   {"buff_target": (2, 2, ()),
                    "buff_hand_minion": (2, 2)}),      # 漂浮教程
        "BE_726": (None, {"unsupported": True}),       # 克苏恩仪式
        "CORE_BOT_222": ("enemy_minion", {"dmg": 4,
                                          "self_damage": 4}),  # 灵魂炸弹
        "CORE_EX1_302": ("enemy_minion", {"dmg": 1,
                                          "draw_on_kill": 1}),  # 死亡缠绕
        "BE_085": (None, {"delay": 0.65,
                          "summon": (2, 3, 5, ("taunt",))}),  # 召唤仪式
        "BE_087": (None, {"summon": (2, 0, 3, ("taunt",)),
                          "assemble": 5.0}),            # 至暗深处
        "CATA_496": ("enemy_minion", {"control": True}),  # 诅咒之链
        "CATA_499": (None, {"summon_random": (1, 2)}), # 助祭耗材
        "CORE_ICC_055": ("enemy_minion", {"dmg": 3,
                                          "lifesteal": 3}),  # 吸取灵魂
        "EDR_483": (None, {"resource": 1.0,
                           "penalty": 2.0}),           # 破碎之力
        "JAIL_515": ("enemy_minion", {"dmg": 2,
                                      "chain_on_kill": True}),  # 暗影轮射
        "BE_005": (None, {"delay": 0.65, "draw": 2}),  # 充盈仪式
        "BE_086": (None, {"aoe_enemies": 2}),          # 污亵
        "CORE_EX1_309": ("enemy_minion", {"destroy": True,
                                          "heal_hero": 3}),  # 灵魂虹吸
        "EDR_490": ("enemy_minion", {"destroy": True}),  # 麻痹睡眠（母卡兜底）
        "EDR_490a": (None, {"summon": (2, 3, 6,
                                       ("taunt", "cantattack"))}),  # 暗处的身影
        "EDR_490b": ("enemy_minion", {"destroy": True}),  # 神智的终结
        "TIME_030": (None, {"resource": 0.0,
                            "penalty": 3.0}),           # 裂解术
        "CATA_491": (None, {"aoe_all_seq": (3, 2, 1)}),  # 怪异触手
        "CORE_EX1_312": (None, {"destroy_all": True}),  # 扭曲虚空
        "JAIL_514": (None, {"draw": 3}),               # 秘密监控
        # ---------------------------------------------------------- 战士
        "EDR_570": (None, {"aoe_all": 1}),              # 凶险梦魇（母卡兜底）
        "EDR_570A": (None, {"aoe_all": 1}),             # 噩梦爆发
        "EDR_570B": ("friendly_minion_damaged",
                     {"buff_target": (2, 2, ())}),       # 动荡能量
        # ---------------------------------------------------------- 中立
        "END_020": ("enemy_minion", {"dmg": 1,
                                     "draw_if_survive": 1,
                                     "summon_if_kill": (1, 1)}),  # 永时困苦
        "END_005": (None, {"summon_random": (4, 1)}),   # 旧时回响
    }

    # ================================================================ 留牌
    @staticmethod
    def choose(state: AiState) -> list[int]:
        """起手留牌：返回需要【换掉】的手牌下标（0 基）。

        规则见 ai/kuaimu.txt「留牌策略」：
          * 必留：寻求平衡、古言术：笞、生命仪式、暮光侵扰；
          * 慢速/中速补留：古言术：预、熵能裂隙；
          * 对快攻补留：麦迪文的胜利；
          * 真言术：盾/愈合只有手里有随从目标（生命仪式）才留；
          * 幽魂不散只在已有随从来源且曲线顺（3费以上不超过1张）时留；
          * 祖拉玛特的仆从/神圣之拥/墓晨太阳花永远不留；
          * 灵质处决/雷斯/捉鬼专家/抹除存在起手不留；
          * 幸运币（0费）不换。
        这里只表达"想换哪几张"；点击坐标由 MulliganFlow 统一过滤。
        """
        hand = list(getattr(state, "my_hand_cards", ()) or ())
        ids = [getattr(card, "card_id", None) for card in hand]
        has_ritual = Kuaimu.CARD_ID_LIFE_RITUAL in ids
        has_minion_target = has_ritual
        three_plus = sum(
            1 for card in hand
            if int(getattr(card, "current_cost", 0) or 0) >= 3)
        aggro = Kuaimu._is_aggro_class(state)

        replace = []
        for index, card in enumerate(hand):
            card_id = getattr(card, "card_id", None)
            cost = int(getattr(card, "current_cost", 0) or 0)
            if cost == 0:
                continue                                  # 幸运币：保留
            if card_id in Kuaimu.MULLIGAN_ALWAYS_KEEP:
                continue
            if card_id in Kuaimu.CARD_IDS_SHIELD or card_id == Kuaimu.CARD_ID_MEND:
                if has_minion_target:
                    continue
                replace.append(index)
                continue
            if card_id in Kuaimu.MULLIGAN_SLOW_KEEP:
                if not aggro:
                    continue
                replace.append(index)
                continue
            if card_id in Kuaimu.MULLIGAN_AGGRO_KEEP:
                if aggro:
                    continue
                replace.append(index)
                continue
            if card_id == Kuaimu.CARD_ID_GHOST_GRIP:
                if has_ritual and three_plus <= 1:
                    continue
                replace.append(index)
                continue
            # 其它（捉鬼专家/抹除存在/灵质处决/雷斯/未知牌）起手换掉。
            replace.append(index)

        names = [
            f"{getattr(hand[index], 'card_id', '?')}"
            f"({Kuaimu.card_name(getattr(hand[index], 'card_id', None)) or '?'})"
            for index in replace
        ]
        print(f"[快牧留牌] 起手 {len(hand)} 张，换掉 {len(replace)} 张：{names}")
        return replace

    @staticmethod
    def _is_aggro_class(state: AiState) -> bool:
        """对手是不是快攻职业（只看英雄卡 cardClass，认不出按前缀兜底）。"""
        hero = getattr(state, "oppo_hero", None)
        card_id = getattr(hero, "card_id", None)
        return Kuaimu._hero_class(card_id) in Kuaimu.AGGRO_CLASSES

    @staticmethod
    def _hero_class(card_id) -> str:
        if not card_id:
            return ""
        try:
            from json_op import query_json_item
            item = query_json_item(card_id) or {}
            value = item.get("cardClass") or item.get("class")
            if value:
                return str(value).upper()
        except Exception:
            pass
        return Kuaimu.HERO_CLASS_BY_PREFIX.get(str(card_id)[:7], "")

    # ================================================================ 上下文
    @staticmethod
    def build_deck_context(state):
        """Search.search() 调用：从真实战局构建快牧上下文。"""
        return KuaimuDeckContext.from_graveyard(
            getattr(state, "my_graveyard", None),
            getattr(state, "my_dead_card_ids", None),
        )

    @staticmethod
    def _context(state) -> KuaimuDeckContext:
        context = getattr(state, "deck_context", None)
        if isinstance(context, KuaimuDeckContext):
            return context
        return KuaimuDeckContext()

    # ================================================================ 搜索辅助
    @staticmethod
    def card_text(card_id) -> str:
        """给 search._card_text 兜底：新卡不在旧 cards.json 时也能推断目标。"""
        text = card_text(card_id)
        if text:
            return text
        try:
            from json_op import query_json_item
            item = query_json_item(card_id) or {}
            return item.get("text") or ""
        except Exception:
            return ""

    @staticmethod
    def card_name(card_id) -> str:
        """给 search 的指令文案兜底：避免新卡触发整表重下载。"""
        return card_item(card_id).get("name") or ""

    # ---------------------------------------------------------------- 目标
    # search._requires_target / _possible_targets 的卡组钩子：按 card_id 精确
    # 接管本卡组的目标规则，不依赖卡面文本（新卡文本可能不在旧 cards.json 里）。
    # 返回 None = 不接管，交给搜索的通用文本推断。
    TARGETED_CARD_IDS = CARD_IDS_SHIELD | frozenset({
        CARD_ID_MEND,                # 愈合：己方随从
        CARD_ID_SERVANT,             # 祖拉玛特的仆从：敌方随从（沉默）
        CARD_ID_HOLY_EMBRACE,        # 神圣之拥：己方随从/英雄
        CARD_ID_DARK_EMBRACE,        # 黑暗之拥：敌方随从/英雄
        CARD_ID_GHOST_GRIP,          # 幽魂不散：己方随从
        CARD_ID_GHOSTHUNTER,         # 捉鬼专家：己方随从
        CARD_ID_TWILIGHT_DESTROY,    # 操纵荆棘：攻击力≤3的敌方随从
    })
    UNTARGETED_CARD_IDS = frozenset({
        CARD_ID_QUEST, CARD_ID_FLAY, CARD_ID_ENTROPIC, CARD_ID_TWILIGHT,
        CARD_ID_TWILIGHT_SUMMON, CARD_ID_LIFE_RITUAL, CARD_ID_FORESIGHT,
        CARD_ID_ERASE, CARD_ID_SUNFLOWER, CARD_ID_EXECUTE, CARD_ID_MEDIVH,
        CARD_ID_RAGS, CARD_ID_SOULTRONUS, CARD_ID_LIFE_HALF,
        CARD_ID_DEATH_HALF,
    })

    @staticmethod
    def requires_target(entity):
        """这张牌是否需要指定目标；认不出返回 None（交给通用文本推断）。"""
        card_id = getattr(entity, "card_id", None)
        if card_id in Kuaimu.TARGETED_CARD_IDS:
            return True
        if card_id in Kuaimu.UNTARGETED_CARD_IDS:
            return False
        if card_id in Kuaimu.RANDOM_SHADOW_CARDS:
            # 熵能裂隙随机出的牌：按表里的目标模式，不依赖可能过时/缺失的卡面文本。
            return Kuaimu.RANDOM_SHADOW_CARDS[card_id][0] is not None
        return None

    @staticmethod
    def possible_targets(entity, state):
        """按 card_id 给出合法目标号位（1 基 AiSlot）；None = 交给通用推断。"""
        card_id = getattr(entity, "card_id", None)
        if card_id in Kuaimu.UNTARGETED_CARD_IDS:
            return ()
        if card_id not in Kuaimu.TARGETED_CARD_IDS:
            entry = Kuaimu.RANDOM_SHADOW_CARDS.get(card_id)
            if entry is not None:
                return Kuaimu._random_shadow_targets(entry[0], state)
            return None

        def minions(side, predicate=None):
            try:
                entries = ordered_board(state, side)
            except Exception:
                return ()
            slots = []
            for position, entry in enumerate(entries, 1):
                if entry.kind != "minion":
                    continue
                if predicate is not None and not predicate(entry.entity):
                    continue
                slots.append(AiSlot("board_slot", side, position))
            return tuple(slots)

        if card_id == Kuaimu.CARD_ID_SERVANT:
            # 沉默敌方关键随从（嘲讽/复生/亡语/buff）
            return minions("enemy")
        if card_id == Kuaimu.CARD_ID_TWILIGHT_DESTROY:
            return minions(
                "enemy", lambda minion: getattr(minion, "attack", 0) <= 3)
        if card_id == Kuaimu.CARD_ID_HOLY_EMBRACE:
            # 恢复4：可奶己方随从或英雄
            return (*minions("friendly"), AiSlot("hero", "friendly", None))
        if card_id == Kuaimu.CARD_ID_DARK_EMBRACE:
            # 造成4：可打敌方随从或敌方英雄
            return (*minions("enemy"), AiSlot("hero", "enemy", None))
        # 愈合 / 真言术：盾 / 幽魂不散 / 捉鬼专家：只指己方随从
        return minions("friendly")

    @staticmethod
    def _random_shadow_targets(mode, state):
        """随机暗影法术的目标模式 -> 候选号位（供 search._possible_targets）。"""
        if mode is None:
            return ()

        def minions(side, predicate=None):
            try:
                entries = ordered_board(state, side)
            except Exception:
                return ()
            return tuple(
                AiSlot("board_slot", side, position)
                for position, entry in enumerate(entries, 1)
                if entry.kind == "minion"
                and (predicate is None or predicate(entry.entity)))

        if mode == "enemy_minion":
            return minions("enemy")
        if mode == "friendly_minion":
            return minions("friendly")
        if mode == "friendly_minion_damaged":
            return minions("friendly", lambda m: getattr(m, "damage", 0) > 0)
        if mode == "any_minion":
            return (*minions("friendly"), *minions("enemy"))
        if mode == "enemy_any":
            return (*minions("enemy"), AiSlot("hero", "enemy", None))
        if mode == "friendly_any":
            return (*minions("friendly"), AiSlot("hero", "friendly", None))
        if mode == "any":
            return (*minions("friendly"), *minions("enemy"),
                    AiSlot("hero", "friendly", None),
                    AiSlot("hero", "enemy", None))
        return ()

    # ================================================================ 场面评分
    @staticmethod
    def rankStaticState(state: AiState) -> float:
        """场面分：随从攻血/关键字 + 双方英雄血量 + 任务完成加成。"""
        res = 0.0
        for minion in getattr(state, "my_minions", []):
            res += Kuaimu._minion_value(minion, mine=True)
            if getattr(minion, "card_id", None) == CARD_ID_SOULTRONUS:
                res += 10.0                        # 循环新生站场 = 制胜点
            elif getattr(minion, "card_id", None) == Kuaimu.CARD_ID_RAGS:
                res += 4.0                         # 雷斯站场 = 麦迪文1费 + 收尾
        for minion in getattr(state, "oppo_minions", []):
            res -= Kuaimu._minion_value(minion, mine=False)
            if getattr(minion, "card_id", None) == CARD_ID_SOULTRONUS:
                res -= 10.0                        # 敌方循环新生是巨大威胁
        my_hero = getattr(state, "my_hero", None)
        oppo_hero = getattr(state, "oppo_hero", None)
        res += getattr(my_hero, "health", 0) * Kuaimu.MY_HERO_WEIGHT
        res -= getattr(oppo_hero, "health", 0) * Kuaimu.ENEMY_HERO_WEIGHT
        if Kuaimu._context(state).quest_complete():
            res += Kuaimu.QUESTS_DONE_STATIC_BONUS
        return res

    # ================================================================ 动作评分
    @staticmethod
    def rankActions(state: AiState, actions: list[AiAction]) -> float:
        """动作增量分：只评"最新那个动作"（列表最后一个）。"""
        if not actions:
            return 0.0
        action = actions[-1]
        if action.action == ActionKind.PLAY_CARD:
            return Kuaimu._play_gain(state, action, actions)
        if action.action == ActionKind.ATTACK:
            return Kuaimu._attack_gain(state, action)
        if action.action == ActionKind.CHOOSE_DISCOVER:
            return Kuaimu._discover_gain(state, action)
        if action.action == ActionKind.USE_HERO_POWER:
            return Kuaimu._hero_power_gain(state, action)
        # 交易/地标/星舰：本卡组没有，保持中性
        return 0.0

    @staticmethod
    def rankState(state: AiState, actions: list[AiAction]) -> float:
        """关键函数：场面分 + 最新动作增量。"""
        return (Kuaimu.rankStaticState(state)
                + Kuaimu.rankActions(state, actions))

    # ---------------------------------------------------------------- 出牌
    @staticmethod
    def _play_gain(state: AiState, action: AiAction,
                   actions: list[AiAction]) -> float:
        """打出一张牌的增量分：硬币/法术/随从分流。"""
        card_id = getattr(action, "card_id", None)
        context = Kuaimu._context(state)
        if Kuaimu._is_coin(card_id):
            return Kuaimu._coin_gain(state, context)
        if action.card_type == "SPELL":
            return Kuaimu._spell_gain(state, action, context, actions)
        if action.card_type == "MINION":
            return Kuaimu._minion_gain(state, action, context, actions)
        return 0.0

    @staticmethod
    def _spell_gain(state: AiState, action: AiAction,
                    context: KuaimuDeckContext,
                    actions: list[AiAction]) -> float:
        """法术分 = 学派任务进度分 + 各牌效果分。"""
        card_id = getattr(action, "card_id", None)
        gain = 0.0

        # 任务进度：对应学派没满 4 个时，施放一做法术推进任务。
        school = spell_school(card_id)
        if school == "HOLY" and context.holy_quest_open():
            gain += Kuaimu.QUEST_CAST_BONUS
        elif school == "SHADOW" and context.shadow_quest_open():
            gain += Kuaimu.QUEST_CAST_BONUS
        # 这一手刚好完成最后一边任务：奖励半身即将到手，额外给分。
        if school == "HOLY" and context.holy_left() == 1:
            gain += Kuaimu.QUEST_COMPLETE_BONUS
        elif school == "SHADOW" and context.shadow_left() == 1:
            gain += Kuaimu.QUEST_COMPLETE_BONUS

        # 古言术：笞/预 的「下一张暗影法术重复」效果。
        # 笞/预 自己接在谁后面由 _flay_gain / _foresight_gain 处理，这里只补
        # 其它暗影法术（暮光侵扰/幽魂不散/黑暗之拥/灵质处决…）的重复收益。
        if (school == "SHADOW" and len(actions) >= 2
                and card_id not in (Kuaimu.CARD_ID_FLAY,
                                    Kuaimu.CARD_ID_FORESIGHT)):
            previous_id = getattr(actions[-2], "card_id", None)
            if previous_id == Kuaimu.CARD_ID_FLAY:
                gain += Kuaimu._random_damage_gain(
                    state, Kuaimu.FLAY_DAMAGE)
            elif previous_id == Kuaimu.CARD_ID_FORESIGHT:
                gain += Kuaimu.FORESIGHT_REPEAT_BONUS
            elif previous_id == Kuaimu.CARD_ID_NOW:
                # 古言术：现 的下一张暗影法术重复召唤5/5吸血。
                gain += Kuaimu.SHADOW_WING_SUMMON_VALUE

        if card_id == CARD_ID_QUEST:
            # 第一回合只上任务；错过第一回合后补拍也值分。
            gain += (Kuaimu.QUEST_FIRST_TURN_BONUS
                     if state.my_total_mana == 1
                     else Kuaimu.QUEST_LATER_BONUS)
        elif card_id == Kuaimu.CARD_ID_FLAY:
            gain += Kuaimu._flay_gain(state, action, actions)
        elif card_id == Kuaimu.CARD_ID_MEND:
            gain += Kuaimu.MEND_DRAW_VALUE
            gain += Kuaimu._heal_gain(state, action, amount=None)
        elif card_id in Kuaimu.CARD_IDS_SHIELD:
            gain += Kuaimu.SHIELD_DRAW_VALUE
            gain += Kuaimu._buff_gain(state, action)
        elif card_id == Kuaimu.CARD_ID_ENTROPIC:
            gain += Kuaimu._entropic_gain(state)
        elif card_id == Kuaimu.CARD_ID_TWILIGHT:
            gain += Kuaimu._twilight_gain(state, action)
        elif card_id == Kuaimu.CARD_ID_LIFE_RITUAL:
            gain += Kuaimu._life_ritual_gain(state)
        elif card_id == Kuaimu.CARD_ID_HOLY_EMBRACE:
            gain += Kuaimu.HOLY_EMBRACE_BASE
            gain += Kuaimu._heal_gain(
                state, action, amount=Kuaimu.HOLY_EMBRACE_HEAL)
            if context.shadow_quest_open():
                gain += Kuaimu.HOLY_EMBRACE_FUEL_BONUS
        elif card_id == Kuaimu.CARD_ID_DARK_EMBRACE:
            gain += Kuaimu._target_damage_gain(state, action, 4)
        elif card_id == Kuaimu.CARD_ID_FORESIGHT:
            gain += Kuaimu._foresight_gain(state, action, actions)
        elif card_id == Kuaimu.CARD_ID_GHOST_GRIP:
            gain += Kuaimu._ghost_grip_gain(state, action, actions)
        elif card_id == Kuaimu.CARD_ID_ERASE:
            gain += Kuaimu._erase_gain(state)
        elif card_id == Kuaimu.CARD_ID_SUNFLOWER:
            gain += Kuaimu._sunflower_gain(state, action)
        elif card_id == Kuaimu.CARD_ID_EXECUTE:
            gain += Kuaimu._execute_gain(state, context)
        elif card_id == Kuaimu.CARD_ID_MEDIVH:
            gain += Kuaimu._medivh_gain(state, action)
        elif card_id in Kuaimu.RANDOM_SHADOW_CARDS:
            # 熵能裂隙随机出的暗影法术：查表按场面估值。
            gain += Kuaimu._random_shadow_gain(state, action)
        elif school == "SHADOW":
            # 表外暗影法术（未来新卡）：按卡面文本兜底，至少比 0.5 强。
            gain += Kuaimu._generic_shadow_gain(state, action)
        else:
            # 其它未建模法术：只吃学派进度分，保底一点。
            gain += 0.5
        return gain

    @staticmethod
    def _minion_gain(state: AiState, action: AiAction,
                     context: KuaimuDeckContext,
                     actions: list[AiAction]) -> float:
        """随从分 = 站场价值 + 关键随从按 kuaimu.txt 的额外价值。"""
        card_id = getattr(action, "card_id", None)
        gain = 0.0
        if action.card is not None:
            gain += Kuaimu._minion_value(action.card, mine=True)

        if card_id == CARD_ID_SOULTRONUS:
            # 循环新生：战吼复制一个8/8；需要1个额外空格（当前最多5格）。
            gain += Kuaimu.SOULTRONUS_KEYWORD_BONUS
            if state.my_board_slot_num <= 5:
                gain += Kuaimu.SOULTRONUS_COPY_VALUE
        elif card_id in (CARD_ID_LIFE_HALF, CARD_ID_DEATH_HALF):
            gain += Kuaimu._half_gain(state, card_id, context)
        elif card_id == Kuaimu.CARD_ID_GHOSTHUNTER:
            gain += Kuaimu._ghosthunter_gain(state, action, actions)
        elif card_id == Kuaimu.CARD_ID_SERVANT:
            gain += Kuaimu._servant_gain(state, action)
        elif card_id == Kuaimu.CARD_ID_RAGS:
            gain += Kuaimu._rags_gain(state, context)
        return gain

    # ---------------------------------------------------------------- 单卡效果
    @staticmethod
    def _flay_gain(state: AiState, action: AiAction,
                   actions: list[AiAction]) -> float:
        """古言术：笞：随机3伤 + 给下一张暗影法术垫重复效果。"""
        gain = Kuaimu._random_damage_gain(state, Kuaimu.FLAY_DAMAGE)
        if len(actions) >= 2:
            previous = actions[-2]
            if getattr(previous, "card_id", None) == Kuaimu.CARD_ID_FORESIGHT:
                # 笞是预的下一张暗影法术：重复预的抽牌/减费。
                gain += Kuaimu.FORESIGHT_REPEAT_BONUS
            elif getattr(previous, "card_id", None) == Kuaimu.CARD_ID_FLAY:
                # 笞重复上一张笞的3伤。
                gain += Kuaimu._random_damage_gain(
                    state, Kuaimu.FLAY_DAMAGE)
            elif getattr(previous, "card_id", None) == Kuaimu.CARD_ID_NOW:
                # 笞是古言术：现的下一张暗影法术：重复召唤5/5吸血。
                gain += Kuaimu.SHADOW_WING_SUMMON_VALUE
        if Kuaimu._has_shadow_followup(
                state, exclude_card_id=Kuaimu.CARD_ID_FLAY,
                budget=state.my_last_mana - action.cost):
            gain += Kuaimu.FLAY_SETUP_BONUS
        return gain

    @staticmethod
    def _foresight_gain(state: AiState, action: AiAction,
                        actions: list[AiAction]) -> float:
        """古言术：预：抽1减2 + 给下一张暗影法术垫重复效果。"""
        gain = Kuaimu.FORESIGHT_DRAW_VALUE + Kuaimu.FORESIGHT_DISCOUNT_VALUE
        if len(actions) >= 2:
            previous = actions[-2]
            if getattr(previous, "card_id", None) == Kuaimu.CARD_ID_FLAY:
                # 预是笞的下一张暗影法术：重复笞的3伤。
                gain += Kuaimu._random_damage_gain(
                    state, Kuaimu.FLAY_DAMAGE)
            elif getattr(previous, "card_id", None) == Kuaimu.CARD_ID_FORESIGHT:
                gain += Kuaimu.FORESIGHT_REPEAT_BONUS
            elif getattr(previous, "card_id", None) == Kuaimu.CARD_ID_NOW:
                # 预是古言术：现的下一张暗影法术：重复召唤5/5吸血。
                gain += Kuaimu.SHADOW_WING_SUMMON_VALUE
        if Kuaimu._has_shadow_followup(
                state, exclude_card_id=Kuaimu.CARD_ID_FORESIGHT,
                budget=state.my_last_mana - action.cost):
            gain += Kuaimu.FORESIGHT_SETUP_BONUS
        if len(getattr(state, "my_hand_cards", ())) >= 8:
            gain -= 5.0
        return gain

    @staticmethod
    def _entropic_gain(state: AiState) -> float:
        """熵能裂隙：1费拿两张暗影法术，补资源+做任务。"""
        gain = Kuaimu.ENTROPIC_BASE
        if len(getattr(state, "my_hand_cards", ())) >= 8:
            gain -= Kuaimu.ENTROPIC_HAND_FULL_PENALTY
        return gain

    @staticmethod
    def _twilight_gain(state: AiState, action: AiAction) -> float:
        """暮光侵扰：按抉择选项分别估值（选项用 choice_card_id 认）。"""
        choice_id = getattr(action, "choice_card_id", None)
        if choice_id == Kuaimu.CARD_ID_TWILIGHT_DESTROY:
            target = Kuaimu._target_entity(state, action)
            if target is None:
                return 0.0
            gain = Kuaimu._minion_value(target, mine=False)
            gain += getattr(target, "health", 0) * 2   #优先解屁股大的
            if "亡语" in Kuaimu.card_text(
                    getattr(target, "card_id", None)):
                gain -= Kuaimu.TWILIGHT_DESTROY_DEATHRATTLE_PENALTY
            return gain
        if choice_id == Kuaimu.CARD_ID_TWILIGHT_SUMMON:
            if state.my_board_slot_num >= 7:
                return -Kuaimu.TWILIGHT_SUMMON_NO_SPACE_PENALTY
            return Kuaimu.TWILIGHT_SUMMON_VALUE
        # 兜底：按有没有目标判断（旧调用方没有 choice_card_id 时）。
        if action.target is not None:
            target = Kuaimu._target_entity(state, action)
            return (Kuaimu._minion_value(target, mine=False)
                    if target is not None else 0.0)
        if state.my_board_slot_num >= 7:
            return -Kuaimu.TWILIGHT_SUMMON_NO_SPACE_PENALTY
        return Kuaimu.TWILIGHT_SUMMON_VALUE

    @staticmethod
    def _life_ritual_gain(state: AiState) -> float:
        """生命仪式：2/3复制 + 发现的3费随从。"""
        gain = (Kuaimu.LIFE_RITUAL_COPY_VALUE
                + Kuaimu.LIFE_RITUAL_DISCOVERED_VALUE)
        if state.my_board_slot_num >= 7:
            gain -= Kuaimu.LIFE_RITUAL_NO_SPACE_PENALTY
        if len(getattr(state, "my_hand_cards", ())) >= 9:
            gain -= 5.0
        return gain

    @staticmethod
    def _ghost_grip_gain(state: AiState, action: AiAction,
                         actions: list[AiAction]) -> float:
        """幽魂不散：+2/+3复生嘲讽；贴循环新生最值，先幽魂后捉鬼可复制。"""
        target = Kuaimu._target_entity(state, action)
        if target is None:
            return -2.0
        gain = (Kuaimu.GHOST_GRIP_STATS
                + Kuaimu.GHOST_GRIP_REBORN
                + Kuaimu.GHOST_GRIP_TAUNT)
        if getattr(target, "card_id", None) == CARD_ID_SOULTRONUS:
            gain += Kuaimu.GHOST_GRIP_KEY_BONUS
        elif getattr(target, "health", 0) <= 1:
            # 1/1 之类站不住的随从，复生/嘲讽收益很低
            gain -= Kuaimu.GHOST_GRIP_SMALL_TARGET_PENALTY
        if (len(actions) >= 2
                and getattr(actions[-2], "card_id", None) == Kuaimu.CARD_ID_GHOSTHUNTER
                and Kuaimu._same_target(actions[-2], action)):
            # 先捉鬼后幽魂：复生白给。
            gain -= Kuaimu.GHOSTHUNTER_WASTE_PENALTY
        if Kuaimu._has_ghosthunter_followup(state, action):
            gain += Kuaimu.GHOST_GRIP_SETUP_BONUS
        return gain

    @staticmethod
    def _ghosthunter_gain(state: AiState, action: AiAction,
                          actions: list[AiAction]) -> float:
        """捉鬼专家：目标已有复生 -> 召唤复制；否则只贴复生。"""
        target = Kuaimu._target_entity(state, action)
        if target is None:
            return 0.0
        already_reborn = (
            bool(getattr(target, "reborn", 0))
            or getattr(target, "card_id", None) == CARD_ID_SOULTRONUS)
        if (len(actions) >= 2
                and getattr(actions[-2], "card_id", None) == Kuaimu.CARD_ID_GHOST_GRIP
                and Kuaimu._same_target(actions[-2], action)):
            already_reborn = True
        key_bonus = (Kuaimu.GHOSTHUNTER_KEY_BONUS
                     if getattr(target, "card_id", None) == CARD_ID_SOULTRONUS
                     else 0.0)
        if already_reborn:
            # 复制一个完整随从（含当前攻血/buff/关键词）。
            return Kuaimu._minion_value(target, mine=True) + key_bonus
        if (getattr(target, "card_id", None) != CARD_ID_SOULTRONUS
                and getattr(target, "health", 0) <= 1):
            # 给1/1贴复生收益很低（仍按价值排序，避免贴给更没用的）
            return (Kuaimu.GHOSTHUNTER_REBORN_GRANT
                    - Kuaimu.GHOSTHUNTER_SMALL_TARGET_PENALTY
                    + Kuaimu._minion_value(target, mine=True)
                    * Kuaimu.GHOSTHUNTER_VALUE_FACTOR)
        # 没有循环新生时优先贴价值高的随从：复生回来的身材/关键词都更值钱。
        return (Kuaimu.GHOSTHUNTER_REBORN_GRANT
                + Kuaimu._minion_value(target, mine=True)
                * Kuaimu.GHOSTHUNTER_VALUE_FACTOR
                + key_bonus)

    @staticmethod
    def _servant_gain(state: AiState, action: AiAction) -> float:
        """祖拉玛特的仆从：沉默敌方关键随从的额外价值（本体价值另算）。"""
        target = Kuaimu._target_entity(state, action)
        if target is None:
            return 0.0
        return Kuaimu._silence_value(target)

    @staticmethod
    def _silence_value(target) -> float:
        """一个随从被沉默的价值：嘲讽/复生/圣盾/亡语/大威胁。"""
        gain = 0.0
        att = getattr(target, "attack", 0) 
        hea = getattr(target, "health", 0) 
        gain += att
        if getattr(target, "taunt", 0):
            gain += Kuaimu.SERVANT_TAUNT_SILENCE
        if getattr(target, "reborn", 0):
            gain += Kuaimu.SERVANT_REBORN_SILENCE
        if getattr(target, "divine_shield", 0):
            gain += Kuaimu.SERVANT_DIVINE_SHIELD_SILENCE
        if "亡语" in Kuaimu.card_text(getattr(target, "card_id", None)):
            gain += Kuaimu.SERVANT_DEATHRATTLE_SILENCE
        if (att >= 5 or hea >= 6):
            gain += Kuaimu.SERVANT_BIG_THREAT
        return gain

    @staticmethod
    def _erase_gain(state: AiState) -> float:
        """抹除存在：随机沉默并消灭一个敌方随从，按期望值估。

        敌方场面越宽，随机命中关键随从的概率越低，给一点方差惩罚；空场
        直接给天价惩罚（这张牌没有目标，打出去是白放）。
        """
        minions = [minion for minion in getattr(state, "oppo_minions", [])
                   if getattr(minion, "health", 0) > 0]
        if not minions:
            return -Kuaimu.ERASE_NO_TARGET_PENALTY
        count = len(minions)
        value = sum(Kuaimu._minion_value(minion, mine=False)
                    for minion in minions) / count
        silence = sum(Kuaimu._silence_value(minion)
                      for minion in minions) / count
        gain = value + silence * 0.5
        if count >= 3:
            gain -= Kuaimu.ERASE_WIDE_BOARD_PENALTY
        return gain

    @staticmethod
    def _sunflower_gain(state: AiState, action: AiAction) -> float:
        """墓晨太阳花：抽2；延系触发时实际费用为2，额外给效率分。"""
        gain = Kuaimu.SUNFLOWER_DRAW_VALUE
        if action.cost <= 2:
            gain += Kuaimu.SUNFLOWER_KINDRED_BONUS
        if len(getattr(state, "my_hand_cards", ())) >= 8:
            gain -= Kuaimu.SUNFLOWER_HAND_FULL_PENALTY
        return gain

    @staticmethod
    def _execute_gain(state: AiState, context: KuaimuDeckContext) -> float:
        """灵质处决：有循环新生时清场吃亡语；没有时只按弱势重置估值。"""
        soultronus_num = sum(
            1 for minion in getattr(state, "my_minions", [])
            if getattr(minion, "card_id", None) == CARD_ID_SOULTRONUS)
        enemy_value = sum(
            Kuaimu._minion_value(minion, mine=False)
            for minion in getattr(state, "oppo_minions", []))
        my_value = sum(
            Kuaimu._minion_value(minion, mine=True)
            for minion in getattr(state, "my_minions", []))
        # 清场本身是双向的：对方也会拿一张3费返场法术，所以只按折扣估值。
        clear = ((enemy_value - my_value) * Kuaimu.EXECUTE_RESET_FACTOR
                 - enemy_value * Kuaimu.EXECUTE_OPPONENT_RESUMMON_FACTOR)
        if soultronus_num == 0:
            return -Kuaimu.EXECUTE_NO_SOULTRONUS_PENALTY + clear
        gain = clear
        gain += soultronus_num * (Kuaimu.EXECUTE_DEATHRATTLE_VALUE
                                  + Kuaimu.EXECUTE_REBORN_VALUE)
        # 其它复生随从清场后也会以1血复活，算一点返场价值。
        for minion in getattr(state, "my_minions", []):
            if (getattr(minion, "reborn", 0)
                    and getattr(minion, "card_id", None) != CARD_ID_SOULTRONUS):
                gain += Kuaimu.EXECUTE_REBORN_VALUE
        return gain

    @staticmethod
    def _medivh_gain(state: AiState, action: AiAction) -> float:
        """麦迪文的胜利：全场打4，敌我一起算净收益；雷斯站场时1费。"""
        if not any(getattr(minion, "health", 0) > 0
                   for minion in getattr(state, "oppo_minions", [])):
            # 敌方空场：这张牌只会伤自己，别为了任务进度空放。
            return -Kuaimu.MEDIVH_NO_ENEMY_PENALTY
        effect = Kuaimu._aoe_damage_gain(state, Kuaimu.MEDIVH_DAMAGE)
        if effect < 0:
            # 净伤自己场面：重罚，别为了任务进度空打这张5费/1费清场。
            return effect * Kuaimu.MEDIVH_NEGATIVE_EFFECT_MULTIPLIER
        gain = effect
        if (action.cost <= 1
                and any(getattr(minion, "card_id", None) == Kuaimu.CARD_ID_RAGS
                        for minion in getattr(state, "my_minions", []))):
            gain += Kuaimu.MEDIVH_CHEAP_BONUS
        return gain

    @staticmethod
    def _rags_gain(state: AiState, context: KuaimuDeckContext) -> float:
        """雷斯·范盖斯特：复活本局复生过的随从；注意7格上限。"""
        pool = context.reborn_death_count()
        free_slots = max(0, 7 - state.my_board_slot_num)
        if pool <= 0 or free_slots <= 0:
            return -Kuaimu.RAGS_NO_POOL_PENALTY
        gain = min(pool, free_slots) * Kuaimu.RAGS_PER_REBORN
        if context.soultronus_died:
            gain += Kuaimu.RAGS_SOULTRONUS_BONUS
        return gain

    @staticmethod
    def _half_gain(state: AiState, card_id,
                   context: KuaimuDeckContext) -> float:
        """任务半身：任务没做完就单出 = 放弃合体，重罚。

        两个任务（4神圣+4暗影）都完成前，生命之息/死亡之触只是半张循环新生；
        先打出去就再也拼不回来，因此给 -500，远高于普通留牌惩罚。
        """
        if not context.quest_complete():
            return -Kuaimu.HALF_QUEST_UNFINISHED_PENALTY
        other = (CARD_ID_DEATH_HALF if card_id == CARD_ID_LIFE_HALF
                 else CARD_ID_LIFE_HALF)
        if any(getattr(card, "card_id", None) == other
               for card in getattr(state, "my_hand_cards", ())):
            return -Kuaimu.HALF_COMBINE_WAIT_PENALTY
        return -Kuaimu.HALF_HOLD_PENALTY

    @staticmethod
    def _coin_gain(state: AiState, context: KuaimuDeckContext) -> float:
        """硬币：刚好点亮一张值得打的「剩余法力+1」牌才出；白出就惩罚。"""
        want = state.my_last_mana + 1
        for card in getattr(state, "my_hand_cards", ()):
            card_id = getattr(card, "card_id", None)
            if Kuaimu._is_coin(card_id):
                continue
            try:
                cost = int(getattr(card, "current_cost", 0) or 0)
            except (TypeError, ValueError):
                continue
            if cost != want:
                continue
            if not Kuaimu._card_worth_enabling(card, state, context):
                continue
            return Kuaimu.COIN_ENABLER_BONUS
        return -Kuaimu.COIN_WASTE_PENALTY

    @staticmethod
    def _card_worth_enabling(card, state: AiState,
                             context: KuaimuDeckContext) -> bool:
        """硬币点亮的这张牌值不值得为它出硬币。"""
        card_id = getattr(card, "card_id", None)
        if card_id in (CARD_ID_LIFE_HALF, CARD_ID_DEATH_HALF):
            return False                                  # 半身要等合体
        if card_id == Kuaimu.CARD_ID_EXECUTE and not any(
                getattr(minion, "card_id", None) == CARD_ID_SOULTRONUS
                for minion in getattr(state, "my_minions", [])):
            return False                                  # 没循环新生别为清场出硬币
        if card_id == Kuaimu.CARD_ID_RAGS and context.reborn_death_count() <= 0:
            return False                                  # 没复活池别为雷斯出硬币
        return True

    # ---------------------------------------------------------------- 发现
    @staticmethod
    def _discover_gain(state: AiState, action: AiAction) -> float:
        """发现/选择：生命仪式优先复生，其次嘲讽/战吼，不选纯白板。

        注意 general_choice_cards 的键是日志里的 0 基序号，而 CHOOSE_DISCOVER
        的号位是 1 基（搜索与执行都按「第 N 个选项」数），必须 index - 1。
        """
        options = getattr(state, "general_choice_cards", None) or {}
        index = getattr(action.source, "index", None)
        card_id = options.get(index - 1) if index else None
        if not card_id:
            return Kuaimu.DISCOVER_DOMINATE_BASE
        score = Kuaimu.DISCOVER_DOMINATE_BASE
        text = Kuaimu.card_text(card_id)
        if "复生" in text:
            score += Kuaimu.DISCOVER_REBORN_BONUS
        if "嘲讽" in text:
            score += Kuaimu.DISCOVER_TAUNT_BONUS
        if "战吼" in text:
            score += Kuaimu.DISCOVER_BATTLECRY_BONUS
        if "亡语" in text:
            score += Kuaimu.DISCOVER_DEATHRATTLE_BONUS
        attack, health = Kuaimu._card_stats(card_id)
        score += (attack * Kuaimu.ATTACK_WEIGHT
                  + health) * Kuaimu.DISCOVER_STAT_FACTOR
        return score

    # ---------------------------------------------------------------- 英雄技能
    @staticmethod
    def _hero_power_gain(state: AiState, action: AiAction) -> float:
        """英雄技能：牧师默认是恢复2，按目标估值；不是治疗就给保底分。"""
        text = Kuaimu.card_text(getattr(action, "card_id", None))
        if "恢复" in text and "生命值" in text:
            gain = Kuaimu._heal_gain(state, action, amount=2)
            if gain <= 0:
                return -1.0                    # 全员满血：别浪费2费
            my_hero = getattr(state, "my_hero", None)
            if getattr(my_hero, "health", 30) <= 15:
                gain += 1.0
            return gain
        if "造成" in text and "点伤害" in text:
            return Kuaimu._target_damage_gain(state, action, 2)
        return 0.5

    # ---------------------------------------------------------------- 攻击
    @staticmethod
    def _attack_gain(state: AiState, action: AiAction) -> float:
        """一次攻击值多少分：打脸按伤害算（斩杀给天价），随从交换按换血算。"""
        attacker = action.card
        target = action.target
        if attacker is None or target is None:
            return 0.0
        attack = getattr(attacker, "attack", 0)
        if attack <= 0:
            return 0.0

        # ---- 打脸
        if target.kind == "hero":
            hero = getattr(state, "oppo_hero", None)
            health = getattr(hero, "health", 0)
            if hero is not None and 0 < health <= attack:
                return Kuaimu.LETHAL_BONUS
            return attack * Kuaimu.ENEMY_HERO_WEIGHT

        # ---- 随从交换
        defender = Kuaimu._board_minion(state, "enemy", target.index)
        if defender is None:
            return 0.0
        attacker_health = getattr(attacker, "health", 0)
        defender_health = getattr(defender, "health", 0)
        defender_attack = getattr(defender, "attack", 0)
        if defender_health <= 0 or attacker_health <= 0:
            return 0.0

        gain = 0.0
        dealt = min(attack, defender_health)
        gain += dealt * Kuaimu.HEALTH_WEIGHT
        if dealt >= defender_health:
            gain += (Kuaimu._minion_value(defender, mine=False)
                     - defender_health * Kuaimu.HEALTH_WEIGHT)
        taken = min(defender_attack, attacker_health)
        gain -= taken * Kuaimu.HEALTH_WEIGHT
        if taken >= attacker_health:
            gain -= (Kuaimu._minion_value(attacker, mine=True)
                     - attacker_health * Kuaimu.HEALTH_WEIGHT)
        return gain

    # ---------------------------------------------------------------- 工具
    @staticmethod
    def _target_entity(state: AiState, action: AiAction):
        """动作目标号位 -> 实体（英雄或随从）；无效返回 None。"""
        target = action.target
        if target is None:
            return None
        if target.kind == "hero":
            if target.owner == "enemy":
                return getattr(state, "oppo_hero", None)
            if target.owner == "friendly":
                return getattr(state, "my_hero", None)
            return None
        return Kuaimu._board_minion(
            state, target.owner or "enemy", target.index)

    @staticmethod
    def _board_minion(state: AiState, side: str, slot):
        """1 基场面号位 -> 随从实体；越界或那一格是地标时返回 None。"""
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
    def _same_target(a: AiAction, b: AiAction) -> bool:
        ta, tb = a.target, b.target
        if ta is None or tb is None:
            return False
        return (ta.kind, ta.owner, ta.index) == (tb.kind, tb.owner, tb.index)

    @staticmethod
    def _minion_value(minion, mine: bool) -> float:
        """一个随从的价值：攻*2 + 血 + 嘲讽/复生/圣盾（我方另加铺场分）。"""
        value = (getattr(minion, "attack", 0) * Kuaimu.ATTACK_WEIGHT
                 + getattr(minion, "health", 0) * Kuaimu.HEALTH_WEIGHT)
        if getattr(minion, "taunt", 0):
            value += Kuaimu.TAUNT_VALUE
        if getattr(minion, "reborn", 0):
            value += Kuaimu.REBORN_VALUE
        if getattr(minion, "divine_shield", 0):
            value += Kuaimu.DIVINE_SHIELD_VALUE
        if mine:
            value += Kuaimu.BOARD_PRESENCE_SCORE
        return value

    @staticmethod
    def _heal_gain(state: AiState, action: AiAction, amount) -> float:
        """恢复类按目标算：缺血越多越值；满血目标 = 白给（小惩罚）。

        amount=None 表示「恢复所有生命值」（愈合）。
        """
        target = action.target
        entity = Kuaimu._target_entity(state, action)
        if entity is None:
            return 0.0
        missing = getattr(entity, "damage", 0) or 0
        if missing <= 0:
            return -1.0
        healed = missing if amount is None else min(amount, missing)
        weight = (Kuaimu.MY_HERO_WEIGHT
                  if target is not None and target.kind == "hero"
                  else Kuaimu.HEALTH_WEIGHT)
        return healed * weight

    @staticmethod
    def _target_damage_gain(state: AiState, action: AiAction,
                            damage: int) -> float:
        """单体伤害按目标算：斩杀天价 > 打死随从拿价值 > 打脸烧血。"""
        target = action.target
        if target is None:
            return damage * Kuaimu.ENEMY_HERO_WEIGHT
        if target.kind == "hero":
            if target.owner != "enemy":
                # 伤自己英雄：纯亏血，不能给正分（灵魂炸弹等随机牌要用）。
                return -damage * Kuaimu.MY_HERO_WEIGHT
            hero = getattr(state, "oppo_hero", None)
            health = getattr(hero, "health", 0)
            if 0 < health <= damage:
                return Kuaimu.LETHAL_BONUS
            return damage * Kuaimu.ENEMY_HERO_WEIGHT
        friendly = target.owner == "friendly"
        minion = Kuaimu._board_minion(
            state, target.owner or "enemy", target.index)
        if minion is None:
            return 0.0
        health = getattr(minion, "health", 0)
        if health <= 0:
            return 0.0
        dealt = min(damage, health)
        gain = dealt * Kuaimu.HEALTH_WEIGHT
        if dealt >= health:
            gain += (Kuaimu._minion_value(minion, mine=friendly)
                     - health * Kuaimu.HEALTH_WEIGHT)
        return -gain if friendly else gain

    @staticmethod
    def _random_damage_gain(state: AiState, damage: int) -> float:
        """随机对敌人打 damage 的期望值（笞/暮光侵扰的随机效果）。"""
        targets = []
        hero = getattr(state, "oppo_hero", None)
        if hero is not None:
            targets.append(("hero", hero))
        for minion in getattr(state, "oppo_minions", []):
            if getattr(minion, "health", 0) > 0:
                targets.append(("minion", minion))
        if not targets:
            return 0.0
        total = 0.0
        for kind, entity in targets:
            health = getattr(entity, "health", 0)
            if kind == "hero":
                if 0 < health <= damage:
                    total += Kuaimu.LETHAL_BONUS
                else:
                    total += damage * Kuaimu.ENEMY_HERO_WEIGHT
                continue
            dealt = min(damage, health)
            value = dealt * Kuaimu.HEALTH_WEIGHT
            if dealt >= health:
                value += (Kuaimu._minion_value(entity, mine=False)
                          - health * Kuaimu.HEALTH_WEIGHT)
            total += value
        return total / len(targets)

    @staticmethod
    def _buff_gain(state: AiState, action: AiAction) -> float:
        """+2生命值类 buff（真言术：盾）按目标算：贴循环新生额外加分。"""
        target = Kuaimu._target_entity(state, action)
        if target is None:
            return 0.0
        gain = Kuaimu.SHIELD_HEALTH_VALUE
        if getattr(target, "card_id", None) == CARD_ID_SOULTRONUS:
            gain += 2.0
        return gain

    @staticmethod
    def _aoe_damage_gain(state: AiState, damage: int) -> float:
        """对所有随从造成 damage：敌方净赚 - 我方净损。"""
        gain = 0.0
        for minion in getattr(state, "oppo_minions", []):
            health = getattr(minion, "health", 0)
            if health <= 0:
                continue
            dealt = min(damage, health)
            gain += dealt * Kuaimu.HEALTH_WEIGHT
            if dealt >= health:
                gain += (Kuaimu._minion_value(minion, mine=False)
                         - health * Kuaimu.HEALTH_WEIGHT)
        for minion in getattr(state, "my_minions", []):
            health = getattr(minion, "health", 0)
            if health <= 0:
                continue
            dealt = min(damage, health)
            gain -= dealt * Kuaimu.HEALTH_WEIGHT
            if dealt >= health:
                gain -= (Kuaimu._minion_value(minion, mine=True)
                         - health * Kuaimu.HEALTH_WEIGHT)
        return gain

    # ---------------------------------------------------------------- 连招辅助
    @staticmethod
    def _has_shadow_followup(state: AiState, exclude_card_id=None,
                             budget=None) -> bool:
        """手里还有别的暗影法术、且费用够接（古言术重复效果的垫牌判断）。"""
        if budget is None:
            budget = state.my_last_mana
        for card in getattr(state, "my_hand_cards", ()):
            card_id = getattr(card, "card_id", None)
            if card_id == exclude_card_id:
                continue
            if spell_school(card_id) != "SHADOW":
                continue
            try:
                cost = int(getattr(card, "current_cost", 0) or 0)
            except (TypeError, ValueError):
                continue
            if 0 <= cost <= budget:
                return True
        return False

    @staticmethod
    def _has_ghosthunter_followup(state: AiState, action: AiAction) -> bool:
        """幽魂不散之后还能接捉鬼专家（同一目标复制）时才给垫牌分。"""
        budget = state.my_last_mana - action.cost
        for card in getattr(state, "my_hand_cards", ()):
            if getattr(card, "card_id", None) != Kuaimu.CARD_ID_GHOSTHUNTER:
                continue
            try:
                cost = int(getattr(card, "current_cost", 0) or 0)
            except (TypeError, ValueError):
                continue
            if 0 <= cost <= budget:
                return True
        return False

    @staticmethod
    def _is_coin(card_id) -> bool:
        """认幸运币：经典币 GAME_005，其它版本 card_id 都带 COIN。"""
        if not card_id:
            return False
        return card_id == "GAME_005" or "COIN" in card_id

    @staticmethod
    def _card_stats(card_id):
        """cards.json -> (attack, health)，发现池性价比用；查不到返回 (0, 0)。"""
        try:
            from json_op import query_json_item
            item = query_json_item(card_id) or {}
            return int(item.get("attack") or 0), int(item.get("health") or 0)
        except Exception:
            return 0, 0

    # ---------------------------------------------------------------- 随机暗影法术
    @staticmethod
    def _random_shadow_gain(state: AiState, action: AiAction) -> float:
        """熵能裂隙随机出的暗影法术：查表按场面估值。

        抉择牌（EDR_490/EDR_570）按 choice_card_id 取选项的效果；表里查不到
        就退回卡面文本兜底，保证任何随机出来的暗影法术都有分、都能用。
        """
        choice_id = getattr(action, "choice_card_id", None)
        entry = Kuaimu.RANDOM_SHADOW_CARDS.get(choice_id)
        if entry is None:
            entry = Kuaimu.RANDOM_SHADOW_CARDS.get(
                getattr(action, "card_id", None))
        if entry is None:
            return Kuaimu._generic_shadow_gain(state, action)
        return Kuaimu._shadow_effects_gain(state, action, entry[1])

    @staticmethod
    def _shadow_effects_gain(state: AiState, action: AiAction,
                             effects: dict) -> float:
        """把效果字典折算成分数：所有数值都按当前场面动态计算。"""
        if effects.get("unsupported"):
            return -Kuaimu.SHADOW_UNSUPPORTED_PENALTY

        gain = effects.get("resource", 0.0) + effects.get("setup", 0.0)
        hand_size = len(getattr(state, "my_hand_cards", ()))

        if "draw" in effects:
            gain += effects["draw"] * Kuaimu.SHADOW_DRAW_VALUE
            if hand_size >= 9:
                gain -= Kuaimu.SHADOW_HAND_FULL_PENALTY
        if "draw_minions" in effects:
            gain += (effects["draw_minions"]
                     * (Kuaimu.SHADOW_DRAW_VALUE + 1.0))
        if "discover_spell" in effects:
            gain += effects["discover_spell"] * Kuaimu.SHADOW_DISCOVER_SPELL
        if "discover_minion" in effects:
            gain += effects["discover_minion"] * Kuaimu.SHADOW_DISCOVER_MINION
        if "refresh_mana" in effects:
            gain += effects["refresh_mana"] * 1.5
        if "dmg" in effects:
            gain += Kuaimu._target_damage_gain(state, action, effects["dmg"])
        if "aoe_enemy" in effects:
            gain += Kuaimu._enemy_aoe_gain(state, effects["aoe_enemy"])
        if "aoe_all" in effects:
            gain += Kuaimu._aoe_damage_gain(state, effects["aoe_all"])
        for amount in effects.get("aoe_all_seq", ()):
            # 连续递减的AOE：每段单独按原始场面估，再打八折避免高估。
            gain += Kuaimu._aoe_damage_gain(state, amount) * 0.8
        if "aoe_enemies" in effects:
            amount = effects["aoe_enemies"]
            gain += Kuaimu._enemy_aoe_gain(state, amount)
            hero = getattr(state, "oppo_hero", None)
            if hero is not None:
                gain += amount * Kuaimu.ENEMY_HERO_WEIGHT
        if "dmg_split_minions" in effects:
            gain += Kuaimu._split_damage_gain(
                state, effects["dmg_split_minions"])
        if "splash" in effects:
            gain += Kuaimu._splash_gain(state, action, effects["splash"])
        if "destroy" in effects:
            gain += Kuaimu._destroy_target_gain(state, action)
            if effects.get("enemy_heal"):
                # 凶煞降临：消灭的随从会给对方英雄回血。
                target = Kuaimu._target_entity(state, action)
                if target is not None:
                    gain -= (getattr(target, "health", 0)
                             * Kuaimu.ENEMY_HERO_WEIGHT)
        if effects.get("destroy_highest"):
            gain += Kuaimu._destroy_highest_gain(state)
        if "destroy_all_attack_ge" in effects:
            gain += Kuaimu._destroy_all_attack_gain(
                state, effects["destroy_all_attack_ge"])
        if effects.get("destroy_all"):
            gain += Kuaimu._destroy_all_gain(state)
        if "heal_hero" in effects:
            gain += Kuaimu._heal_hero_gain(state, effects["heal_hero"])
        if "lifesteal" in effects:
            gain += Kuaimu._lifesteal_gain(state, effects["lifesteal"])
        if "self_damage" in effects:
            gain -= effects["self_damage"] * Kuaimu.MY_HERO_WEIGHT
        if "summon" in effects:
            gain += Kuaimu._summon_gain(state, *effects["summon"])
        if "summon_random" in effects:
            gain += Kuaimu._summon_random_gain(
                state, *effects["summon_random"],
                taunt=effects.get("taunt", False))
        if "buff_target" in effects:
            gain += Kuaimu._shadow_buff_gain(
                state, action, effects["buff_target"])
        if effects.get("copy_target"):
            target = Kuaimu._target_entity(state, action)
            if target is not None:
                gain += Kuaimu._minion_value(target, mine=True)
        if "buff_all" in effects:
            gain += Kuaimu._shadow_buff_all_gain(state, effects["buff_all"])
        if "hand_buff" in effects:
            gain += Kuaimu._shadow_hand_buff_gain(state, effects["hand_buff"])
        if "buff_hand_minion" in effects:
            attack, health = effects["buff_hand_minion"]
            gain += (attack * Kuaimu.ATTACK_WEIGHT + health) * 0.6
        if effects.get("bounce"):
            target = Kuaimu._target_entity(state, action)
            if target is not None:
                gain += Kuaimu._minion_value(target, mine=False) * 0.55
        if effects.get("control"):
            target = Kuaimu._target_entity(state, action)
            if target is not None:
                gain += Kuaimu._minion_value(target, mine=False) * 0.7
        if "draw_on_kill" in effects:
            gain += (effects["draw_on_kill"]
                     * Kuaimu.SHADOW_DRAW_VALUE * 0.5)
        if effects.get("copy_on_kill"):
            gain += 4.0
        if effects.get("chain_on_kill"):
            gain += 3.0
        if "draw_if_survive" in effects:
            gain += (effects["draw_if_survive"]
                     * Kuaimu.SHADOW_DRAW_VALUE * 0.5)
        if "summon_if_kill" in effects:
            gain += Kuaimu._summon_random_gain(
                state, *effects["summon_if_kill"])

        gain *= effects.get("delay", 1.0)
        gain += effects.get("penalty", 0.0)
        return gain

    @staticmethod
    def _generic_shadow_gain(state: AiState, action: AiAction) -> float:
        """表外暗影法术的文本兜底：解析伤害/治疗/抽牌/召唤，至少给 1 分。

        熵能裂隙的池子会随版本变化，新卡在这里也不至于被当成 0.5 分的废牌。
        """
        text = Kuaimu.card_text(getattr(action, "card_id", None))
        gain = 1.0
        target = Kuaimu._target_entity(state, action)
        numeric = {"一": 1, "两": 2, "三": 3, "四": 4, "五": 5}

        damage = re.search(r"造成\D{0,3}(\d+)点伤害", text)
        if damage:
            amount = int(damage.group(1))
            if "所有" in text:
                gain += abs(Kuaimu._aoe_damage_gain(state, amount)) * 0.5
            elif "随机" in text:
                gain += Kuaimu._random_damage_gain(state, amount)
            elif target is not None:
                gain += Kuaimu._target_damage_gain(state, action, amount)
            else:
                gain += amount
        heal = re.search(r"恢复\D{0,3}(\d+)点生命值", text)
        if heal:
            gain += Kuaimu._heal_gain(state, action, int(heal.group(1)))
        draw = re.search(r"抽(\d+|[一二两三四五])张牌", text)
        if draw:
            count = draw.group(1)
            gain += (int(count) if count.isdigit()
                     else numeric.get(count, 1)) * Kuaimu.SHADOW_DRAW_VALUE
        if "召唤" in text:
            gain += 3.0
        if "消灭" in text and target is not None:
            gain += Kuaimu._destroy_target_gain(state, action) * 0.5
        return max(1.0, gain)

    @staticmethod
    def _enemy_aoe_gain(state: AiState, damage: int) -> float:
        """只打敌方随从的AOE（不含英雄）。"""
        gain = 0.0
        for minion in getattr(state, "oppo_minions", []):
            health = getattr(minion, "health", 0)
            if health <= 0:
                continue
            dealt = min(damage, health)
            gain += dealt * Kuaimu.HEALTH_WEIGHT
            if dealt >= health:
                gain += (Kuaimu._minion_value(minion, mine=False)
                         - health * Kuaimu.HEALTH_WEIGHT)
        return gain

    @staticmethod
    def _split_damage_gain(state: AiState, damage: int) -> float:
        """damage 随机分配到敌方随从：按从残血到满血的顺序近似最优分配。"""
        minions = sorted(
            (minion for minion in getattr(state, "oppo_minions", [])
             if getattr(minion, "health", 0) > 0),
            key=lambda minion: getattr(minion, "health", 0))
        gain = 0.0
        remaining = damage
        for minion in minions:
            if remaining <= 0:
                break
            health = getattr(minion, "health", 0)
            dealt = min(remaining, health)
            gain += dealt * Kuaimu.HEALTH_WEIGHT
            remaining -= dealt
            if dealt >= health:
                gain += (Kuaimu._minion_value(minion, mine=False)
                         - health * Kuaimu.HEALTH_WEIGHT)
        return gain

    @staticmethod
    def _splash_gain(state: AiState, action: AiAction, damage: int) -> float:
        """裂地黑暗：目标受伤后向左右两侧溅射（每次少1点）。"""
        slot = action.target
        if slot is None or slot.kind != "board_slot" or not slot.index:
            return 0.0
        try:
            entries = ordered_board(state, slot.owner or "enemy")
        except Exception:
            return 0.0
        gain = Kuaimu._target_damage_gain(state, action, damage)
        sign = 1.0 if slot.owner == "enemy" else -1.0
        splash = max(0, damage - 1)
        for position in (slot.index - 2, slot.index):
            if not 0 <= position < len(entries):
                continue
            entry = entries[position]
            if entry.kind != "minion":
                continue
            health = getattr(entry.entity, "health", 0)
            if health <= 0:
                continue
            dealt = min(splash, health)
            value = dealt * Kuaimu.HEALTH_WEIGHT
            if dealt >= health:
                value += (Kuaimu._minion_value(entry.entity, mine=False)
                          - health * Kuaimu.HEALTH_WEIGHT)
            gain += sign * value
        return gain

    @staticmethod
    def _destroy_target_gain(state: AiState, action: AiAction) -> float:
        """消灭目标：只对敌方随从给正分；指到自己随从是重亏。"""
        target = Kuaimu._target_entity(state, action)
        slot = action.target
        if target is None or slot is None or slot.kind == "hero":
            return 0.0
        if slot.owner == "friendly":
            return -Kuaimu._minion_value(target, mine=True)
        return Kuaimu._minion_value(target, mine=False) + 2.0

    @staticmethod
    def _destroy_highest_gain(state: AiState) -> float:
        """窒息：消灭攻击力最高的敌方随从（没目标时按最强的一个估）。"""
        minions = [minion for minion in getattr(state, "oppo_minions", [])
                   if getattr(minion, "health", 0) > 0]
        if not minions:
            return 0.0
        target = max(minions, key=lambda minion: getattr(minion, "attack", 0))
        return Kuaimu._minion_value(target, mine=False) + 2.0

    @staticmethod
    def _destroy_all_attack_gain(state: AiState, threshold: int) -> float:
        """暗言术：毁：消灭所有攻击力≥threshold的随从（敌我双向结算）。"""
        gain = 0.0
        for minion in getattr(state, "oppo_minions", []):
            if (getattr(minion, "attack", 0) >= threshold
                    and getattr(minion, "health", 0) > 0):
                gain += Kuaimu._minion_value(minion, mine=False)
        for minion in getattr(state, "my_minions", []):
            if (getattr(minion, "attack", 0) >= threshold
                    and getattr(minion, "health", 0) > 0):
                gain -= Kuaimu._minion_value(minion, mine=True)
        return gain

    @staticmethod
    def _destroy_all_gain(state: AiState) -> float:
        """扭曲虚空：敌我全场消灭，按净场面差估值。"""
        enemy_value = sum(
            Kuaimu._minion_value(minion, mine=False)
            for minion in getattr(state, "oppo_minions", [])
            if getattr(minion, "health", 0) > 0)
        my_value = sum(
            Kuaimu._minion_value(minion, mine=True)
            for minion in getattr(state, "my_minions", [])
            if getattr(minion, "health", 0) > 0)
        return (enemy_value - my_value) * 0.9

    @staticmethod
    def _heal_hero_gain(state: AiState, amount: int) -> float:
        hero = getattr(state, "my_hero", None)
        missing = getattr(hero, "damage", 0) or 0
        return min(amount, missing) * Kuaimu.MY_HERO_WEIGHT

    @staticmethod
    def _lifesteal_gain(state: AiState, amount: int) -> float:
        """吸血：按英雄实际缺的血估，满血时不额外加分。"""
        return Kuaimu._heal_hero_gain(state, amount)

    @staticmethod
    def _summon_gain(state: AiState, count: int, attack: int,
                     health: int, keywords=()) -> float:
        """召唤固定身材的随从：按空格数、身材和关键字估值。"""
        free = max(0, 7 - getattr(state, "my_board_slot_num", 0))
        if free <= 0:
            return -Kuaimu.SHADOW_NO_SPACE_PENALTY
        each = (attack * Kuaimu.ATTACK_WEIGHT
                + health * Kuaimu.HEALTH_WEIGHT
                + Kuaimu.BOARD_PRESENCE_SCORE)
        for keyword in keywords:
            each += Kuaimu.SHADOW_KEYWORD_VALUES.get(keyword, 0.0)
        return min(count, free) * each

    @staticmethod
    def _summon_random_gain(state: AiState, cost: int, count: int,
                            taunt: bool = False) -> float:
        """随机召唤指定费用的随从：按费用估身材期望值。"""
        free = max(0, 7 - getattr(state, "my_board_slot_num", 0))
        if free <= 0:
            return -Kuaimu.SHADOW_NO_SPACE_PENALTY
        each = 3.0 + cost * 3.0 + (Kuaimu.TAUNT_VALUE if taunt else 0.0)
        return min(count, free) * each

    @staticmethod
    def _shadow_buff_gain(state: AiState, action: AiAction, spec) -> float:
        """给目标随从加属性：只对己方随从给正分。"""
        attack, health, keywords = spec
        slot = action.target
        target = Kuaimu._target_entity(state, action)
        if target is None or slot is None or slot.kind == "hero":
            return 0.0
        value = attack * Kuaimu.ATTACK_WEIGHT + health * Kuaimu.HEALTH_WEIGHT
        for keyword in keywords:
            value += Kuaimu.SHADOW_KEYWORD_VALUES.get(keyword, 0.0)
        if getattr(target, "card_id", None) == CARD_ID_SOULTRONUS:
            value += 2.0
        return value if slot.owner == "friendly" else -value

    @staticmethod
    def _shadow_buff_all_gain(state: AiState, spec) -> float:
        """使所有己方随从获得属性。"""
        attack, health, keywords = spec
        per = attack * Kuaimu.ATTACK_WEIGHT + health * Kuaimu.HEALTH_WEIGHT
        for keyword in keywords:
            per += Kuaimu.SHADOW_KEYWORD_VALUES.get(keyword, 0.0)
        return len(getattr(state, "my_minions", ())) * per

    @staticmethod
    def _shadow_hand_buff_gain(state: AiState, spec) -> float:
        """手牌随从集体加属性：手里随从越多越值。"""
        attack, health = spec
        count = sum(
            1 for card in getattr(state, "my_hand_cards", ())
            if getattr(card, "cardtype", None) == "MINION")
        return count * (attack * Kuaimu.ATTACK_WEIGHT
                        + health * Kuaimu.HEALTH_WEIGHT) * 0.6
