# 动物园术（铺场术）专属数据与卡组上下文。
#
# 与 ai/任务牧/kuaimu_context.py 同一分工：
#   * CARD_ATTRIBUTES：人工维护的卡牌属性（名称/类型/费用/身材/关键字/卡面文本），
#     评分与目标推断一律按 card_id 查表，卡名只用于日志/注释（用户要求）。
#   * DwysDeckContext：继承 search.ai_state.DeckContext，记录场面之外的对局进度，
#     搜索模拟在 copy() 出来的分支上下文上推进，互不污染。
#
# 动物园术需要追踪的进度（对应 ai/动物园术/动物园术.txt 第六节）：
#   tears_in_deck        牌库中“时空撕裂”(TIME_025t) 的近似数量
#   tears_drawn          已经抽到并自伤的撕裂张数
#   herald_count         已触发“兆示”的次数（打出 CATA_725 的次数）
#   ritual_pending       挂起的克苏恩仪式 BE_726
#   summon_ritual_pending 挂起的召唤仪式 BE_085
#   trial_pending        黑幕庭审是否挂起（CAP_405）
from collections import Counter

from ai.card_rules import (
    CUTHUN_ABILITY_MAX_PER_TURN,
    CUTHUN_ABILITY_ORDER,
    CUTHUN_BODY_CARD_ID,
)
from search.ai_state import DeckContext

# ---------------------------------------------------------------- card_id 常量
CARD_ID_CRYPT = "TLC_451"            # 咒怨之墓
CARD_ID_PROTOTYPE = "BE_207"         # 不稳定的原型机
CARD_ID_CUTHUN_RITUAL = "BE_726"     # 克苏恩仪式
CARD_ID_EGG = "CATA_210"             # 暮光龙卵
CARD_ID_EGG_TOKEN = "CATA_210t"      # 速生雏龙
CARD_ID_PTERRORDAX = "TLC_603"       # 栉龙
CARD_ID_TUTORIAL = "BE_094"          # 漂浮教程
CARD_ID_ENTROPY = "TIME_026"         # 续连熵能
CARD_ID_TEAR = "TIME_025t"           # 时空撕裂（衍生）
CARD_ID_SUMMON_RITUAL = "BE_085"     # 召唤仪式
CARD_ID_KRATHESI = "BE_085t"         # 克拉西斯蛮兵（衍生）
CARD_ID_CULT_PRIEST = "SCH_713"      # 异教低阶牧师（经典版）
CARD_ID_CULT_PRIEST_CORE = "CORE_SCH_713"  # 异教低阶牧师（核心版）
CARD_ID_HERALD = "CATA_725"          # 暗誓信徒
CARD_ID_SOLDIER = "CATA_725t"        # 古加尔的士兵（衍生）
CARD_ID_TIMEWARP = "TIME_025"        # 暮光时空跃迁者
CARD_ID_RITUALIST = "BE_729"         # 喉魔异教徒
CARD_ID_KAZAKUS = "CAP_405"          # 教父卡扎库斯
CARD_ID_DEFILE = "BE_086"            # 污亵
CARD_ID_ZAKAZ = "BE_728"             # 腐蚀者扎卡兹
CARD_ID_DRAKE = "TIME_029"           # 灾毁迅疾幼龙
CARD_ID_VAULT_GUARD = "BE_151"       # 库区守卫
CARD_ID_GLACIER = "CORE_UNG_205"     # 冰川裂片（核心版，标准模式）
CARD_ID_GLACIER_LEGACY = "UNG_205"   # 冰川裂片（经典版，日志兜底）
CARD_ID_SAVIS = "EDR_856"            # 梦魇之王萨维斯
CARD_ID_KISHIX = "BE_090"            # 基希克斯
CARD_ID_SIN_FAMINE = "BE_090t1"      # 饥荒仪式
CARD_ID_SIN_DOMINION = "BE_090t2"    # 支配仪式
CARD_ID_SIN_CONQUEST = "BE_090t3"    # 征服仪式
# 凯洛斯的蛋及破壳衍生：打掉只会加速孵化，未破壳（非嘲讽）时不要攻击。
CARD_IDS_KELTHUZAD_EGG = frozenset({
    "DINO_410",      # 凯洛斯的蛋
    "DINO_410t",     # 凯洛斯（20/20 嘲讽，已破壳）
    "DINO_410t2",    # 开裂加剧的蛋
    "DINO_410t3",    # 严重开裂的蛋
    "DINO_410t4",    # 完全裂开的蛋
    "DINO_410t5",    # 破壳即可孵化
})

# 克苏恩，帝国之眼：本体与 4 个技能（顺序与技能面板从左到右一致，
# 执行层按这个下标换算面板坐标）。规则统一定义在 ai/card_rules.py，
# 这里只保留本卡组沿用的常量别名。
CARD_ID_CUTHUN_FURY, CARD_ID_CUTHUN_DOMAIN, \
    CARD_ID_CUTHUN_GAZE, CARD_ID_CUTHUN_SERVANT = CUTHUN_ABILITY_ORDER
CARD_ID_CUTHUN = CUTHUN_BODY_CARD_ID
CUTHUN_ABILITY_COUNT = len(CUTHUN_ABILITY_ORDER)
CARD_ID_YOGG_SARON = "BE_009"       # 古神主谋尤格-萨隆
CARD_ID_NZOTH = "BE_017"            # 恩佐斯，梦境之末
CARD_ID_YSHARAJ = "BE_784"          # 亚煞极，泰坦杀神

CARD_ID_TRIAL_DETAIN = "CAP_405t1"   # 拘留待毁
CARD_ID_TRIAL_CONSPIRE = "CAP_405t2"  # 合谋获刑
CARD_ID_TRIAL_SMUGGLE = "CAP_405t3"  # 走私罪责
CARD_ID_TRIAL_CONTRABAND = "CAP_405t4"  # 违禁物品
CARD_ID_TRIAL_BLADE = "CAP_405t5"    # 可疑快刀
CARD_ID_TRIAL_PACT = "CAP_405t6"     # 邪恶协议
CARD_ID_TRIAL_FORGERY = "CAP_405t7"  # 假证精华
CARD_ID_TRIAL_POTION = "CAP_405t8"   # 诱导饮剂
CARD_ID_TRIAL_TYRANT = "CAP_405t9"   # 暴君补剂

CARD_ID_TRIAL_FAST = "CAP_405tb1"    # 快速庭审（7 费，立即结束）
CARD_ID_TRIAL_SLOW = "CAP_405tb2"    # 艰涩庭审（4 费，下回合结束）
CARD_ID_TRIAL_ENDLESS = "CAP_405tb3"  # 无尽庭审（0 费，4 回合后）

# 庭审时长在第三轮选择菜单里用的是带 b 后缀的“选项版”卡牌 ID
# （CAP_405tb1b / CAP_405tb2b / CAP_405tb3b）。卡名、费用与效果和正式版
# 完全一致，只是日志里菜单显示的是选项版；不登记的话选第三轮时三个选项
# 都查不到属性 -> 同分 0 -> 搜索只会推荐结束回合（实测 2026-10-23 14:26:11）。
CARD_ID_TRIAL_FAST_OPTION = "CAP_405tb1b"    # 快速庭审（选择菜单选项）
CARD_ID_TRIAL_SLOW_OPTION = "CAP_405tb2b"    # 艰涩庭审（选择菜单选项）
CARD_ID_TRIAL_ENDLESS_OPTION = "CAP_405tb3b"  # 无尽庭审（选择菜单选项）

# 庭审效果的稳妥优先级：同分时按这个顺序挑（见 txt 第四节 B）。
TRIAL_EFFECT_PRIORITY = {
    CARD_ID_TRIAL_TYRANT: 9,
    CARD_ID_TRIAL_PACT: 8,
    CARD_ID_TRIAL_BLADE: 7,
    CARD_ID_TRIAL_SMUGGLE: 6,
    CARD_ID_TRIAL_CONTRABAND: 5,
    CARD_ID_TRIAL_FORGERY: 4,
    CARD_ID_TRIAL_CONSPIRE: 3,
    CARD_ID_TRIAL_POTION: 2,
    CARD_ID_TRIAL_DETAIN: 1,
}

# ---------------------------------------------------------------- 卡牌属性表
# 字段说明：
#   name / type / school / text —— 卡名、类型、法术学派、卡面中文（目标推断用）
#   targeted                    —— 需要指定目标（只在本卡组内使用）
#   cost / attack / health       —— 人工身材（手牌实体在模拟里攻血可能为 0）
#   taunt/reborn/rush/...        —— 关键字（手牌实体可能没有标签，评分兜底）
# 文本只保留“评分与目标判断需要”的官方效果，占位符已按衍生牌补成实际数值。
CARD_ATTRIBUTES = {
    # ------------------------------------------------------------ 0 费
    CARD_ID_CRYPT: {
        "name": "咒怨之墓", "type": "SPELL", "school": "FEL", "cost": 0,
        "text": "从你的牌库中发现另一张牌，将其变为临时卡牌。",
    },
    CARD_ID_TEAR: {
        "name": "时空撕裂", "type": "SPELL", "school": "ARCANE", "cost": 0,
        "text": "抽到时施放：对你的英雄造成3点伤害。",
    },

    # ------------------------------------------------------------ 1 费
    CARD_ID_PROTOTYPE: {
        "name": "不稳定的原型机", "type": "MINION", "cost": 1,
        "attack": 0, "health": 1, "reborn": 1,
        "text": "复生。亡语：使你手牌中的所有随从牌获得+1/+1。",
    },
    CARD_ID_CUTHUN_RITUAL: {
        "name": "克苏恩仪式", "type": "SPELL", "school": "SHADOW", "cost": 1,
        "text": "2回合后，选择并触发一项克苏恩的技能。",
    },
    CARD_ID_EGG: {
        "name": "暮光龙卵", "type": "MINION", "cost": 1,
        "attack": 0, "health": 2,
        "text": "亡语：召唤一条2/1的雏龙。（在你的回合开始时获得+1/+1！）",
    },
    CARD_ID_EGG_TOKEN: {
        "name": "速生雏龙", "type": "MINION", "cost": 1,
        "attack": 2, "health": 1,
        "text": "在你的回合开始时获得+1/+1。",
    },
    CARD_ID_PTERRORDAX: {
        "name": "栉龙", "type": "MINION", "cost": 1,
        "attack": 1, "health": 2,
        "text": "战吼：抽一张牌。亡语：弃掉该牌。",
    },
    CARD_ID_TUTORIAL: {
        "name": "漂浮教程", "type": "SPELL", "school": "SHADOW", "cost": 1,
        "targeted": True,
        "text": "使一个随从和你手牌中的一张随机随从牌获得等同于本牌法力值"
                "消耗双倍的属性值。",
    },
    CARD_ID_ENTROPY: {
        "name": "续连熵能", "type": "SPELL", "school": "ARCANE", "cost": 1,
        "text": "使你的随从获得+1/+1。将2张时空撕裂洗入你的牌库。",
    },

    # ------------------------------------------------------------ 2 费
    CARD_ID_SUMMON_RITUAL: {
        "name": "召唤仪式", "type": "SPELL", "school": "SHADOW", "cost": 2,
        "text": "2回合后，召唤两个3/5并具有嘲讽的克拉西斯。",
    },
    CARD_ID_KRATHESI: {
        "name": "克拉西斯蛮兵", "type": "MINION", "cost": 3,
        "attack": 3, "health": 5, "taunt": 1,
        "text": "嘲讽。",
    },
    CARD_ID_CULT_PRIEST: {
        "name": "异教低阶牧师", "type": "MINION", "cost": 2,
        "attack": 3, "health": 2,
        "text": "战吼：下个回合你的对手法术的法力值消耗增加（1）点。",
    },
    CARD_ID_CULT_PRIEST_CORE: {
        "name": "异教低阶牧师", "type": "MINION", "cost": 2,
        "attack": 3, "health": 2,
        "text": "战吼：下个回合你的对手法术的法力值消耗增加（1）点。",
    },
    CARD_ID_HERALD: {
        "name": "暗誓信徒", "type": "MINION", "cost": 2,
        "attack": 2, "health": 1,
        "text": "战吼：兆示。亡语：为你的英雄恢复3点生命值。",
    },
    CARD_ID_SOLDIER: {
        "name": "古加尔的士兵", "type": "MINION", "cost": 1,
        "attack": 1, "health": 1,
        "text": "在你的回合结束时，消灭本随从右边的随从以获得+1/+1。",
    },
    CARD_ID_TIMEWARP: {
        "name": "暮光时空跃迁者", "type": "MINION", "cost": 2,
        "attack": 4, "health": 4,
        "text": "战吼：将2张时空撕裂洗入你的牌库。"
                "抽到时空撕裂时会对你的英雄造成3点伤害。",
    },

    # ------------------------------------------------------------ 3 费
    CARD_ID_RITUALIST: {
        "name": "喉魔异教徒", "type": "MINION", "cost": 3,
        "attack": 4, "health": 4,
        "text": "所有法术的法力值消耗增加（1）点。",
    },
    CARD_ID_KAZAKUS: {
        "name": "教父卡扎库斯", "type": "MINION", "cost": 3,
        "attack": 3, "health": 3,
        "text": "战吼：谋划一场自定义的黑幕庭审，然后选择庭审的长度！",
    },

    # ------------------------------------------------------------ 4 费
    CARD_ID_DEFILE: {
        "name": "污亵", "type": "SPELL", "school": "SHADOW", "cost": 4,
        "text": "对所有敌人造成2点伤害。"
                "如果本牌为你手牌中法力值消耗最高的牌，重复一次。",
    },
    CARD_ID_ZAKAZ: {
        "name": "腐蚀者扎卡兹", "type": "MINION", "cost": 4,
        "attack": 5, "health": 5,
        "text": "战吼：使你手牌和牌库中的随从牌法力值消耗减少（1）点，"
                "法术牌的法力值消耗增加（1）点。",
    },

    # ------------------------------------------------------------ 5 费
    CARD_ID_DRAKE: {
        "name": "灾毁迅疾幼龙", "type": "MINION", "cost": 5,
        "attack": 5, "health": 5, "rush": 1,
        "text": "突袭。战吼：从你的牌库中施放一张时空撕裂以召唤一个本随从的复制。",
    },
    CARD_ID_VAULT_GUARD: {
        "name": "库区守卫", "type": "MINION", "cost": 5,
        "attack": 3, "health": 3,
        "text": "战吼，亡语：使相邻的随从获得+2/+2。",
    },

    # ------------------------------------------------------------ 凯洛斯的蛋
    "DINO_410": {
        "name": "凯洛斯的蛋", "type": "MINION", "cost": 3,
        "attack": 0, "health": 3,
        "text": "亡语：召唤一枚轻微开裂的蛋。破壳5次后孵化为20/20嘲讽野兽。",
    },
    "DINO_410t": {
        "name": "凯洛斯", "type": "MINION", "cost": 10,
        "attack": 20, "health": 20, "taunt": 1,
        "text": "嘲讽。",
    },
    "DINO_410t2": {
        "name": "凯洛斯的蛋", "type": "MINION", "cost": 3,
        "attack": 0, "health": 3,
        "text": "亡语：召唤一枚开裂加剧的蛋。再破壳4次即可孵化。",
    },
    "DINO_410t3": {
        "name": "凯洛斯的蛋", "type": "MINION", "cost": 3,
        "attack": 0, "health": 3,
        "text": "亡语：召唤一枚严重开裂的蛋。再破壳3次即可孵化。",
    },
    "DINO_410t4": {
        "name": "凯洛斯的蛋", "type": "MINION", "cost": 3,
        "attack": 0, "health": 3,
        "text": "亡语：召唤一枚完全裂开的蛋。再破壳2次即可孵化。",
    },
    "DINO_410t5": {
        "name": "凯洛斯的蛋", "type": "MINION", "cost": 3,
        "attack": 0, "health": 3,
        "text": "亡语：召唤一只20/20并具有嘲讽的凯洛斯。破壳即可孵化。",
    },

    # ------------------------------------------------------------ 克苏恩技能
    CARD_ID_CUTHUN_FURY: {
        "name": "克苏恩的愤怒", "type": "SPELL", "cost": 0,
        "text": "造成7点伤害，随机分配到所有敌人身上。",
    },
    CARD_ID_CUTHUN_DOMAIN: {
        "name": "克苏恩的领域", "type": "SPELL", "cost": 2,
        "text": "随机使两个其他友方随从获得+2/+3。",
    },
    CARD_ID_CUTHUN_GAZE: {
        "name": "克苏恩的凝视", "type": "SPELL", "cost": 0,
        "text": "吸血，对一个敌方随从造成5点伤害。",
    },
    CARD_ID_CUTHUN_SERVANT: {
        "name": "克苏恩的仆从", "type": "SPELL", "cost": 2,
        "text": "召唤两个2/3并具有嘲讽的狂热者。",
    },

    # ------------------------------------------------------------ 庭审效果
    CARD_ID_TRIAL_DETAIN: {
        "name": "拘留待毁", "type": "SPELL", "cost": 0,
        "text": "迫使每个随从攻击另一个随从。",
    },
    CARD_ID_TRIAL_CONSPIRE: {
        "name": "合谋获刑", "type": "SPELL", "cost": 0,
        "text": "随机夺取一个敌方随从的控制权。",
    },
    CARD_ID_TRIAL_SMUGGLE: {
        "name": "走私罪责", "type": "SPELL", "cost": 0,
        "text": "偷取你对手的2张手牌。",
    },
    CARD_ID_TRIAL_CONTRABAND: {
        "name": "违禁物品", "type": "SPELL", "cost": 0,
        "text": "抽三张牌。",
    },
    CARD_ID_TRIAL_BLADE: {
        "name": "可疑快刀", "type": "SPELL", "cost": 0,
        "text": "使你手牌中和战场上的随从获得+3/+3。",
    },
    CARD_ID_TRIAL_PACT: {
        "name": "邪恶协议", "type": "SPELL", "cost": 0,
        "text": "随机召唤三个法力值消耗为（3）的随从。",
    },
    CARD_ID_TRIAL_FORGERY: {
        "name": "假证精华", "type": "SPELL", "cost": 0,
        "text": "使你手牌中随从牌的法力值消耗减少（2）点。",
    },
    CARD_ID_TRIAL_POTION: {
        "name": "诱导饮剂", "type": "SPELL", "cost": 0,
        "text": "为你的英雄恢复12点生命值。",
    },
    CARD_ID_TRIAL_TYRANT: {
        "name": "暴君补剂", "type": "SPELL", "cost": 0,
        "text": "召唤一个虚空领主（3/9 嘲讽，亡语召唤三个1/3嘲讽）。",
    },

    # ------------------------------------------------------------ 庭审时长
    CARD_ID_TRIAL_FAST: {
        "name": "快速庭审", "type": "SPELL", "cost": 7,
        "text": "庭审立即结束。",
    },
    CARD_ID_TRIAL_SLOW: {
        "name": "艰涩庭审", "type": "SPELL", "cost": 4,
        "text": "你的下个回合开始时，庭审结束。",
    },
    CARD_ID_TRIAL_ENDLESS: {
        "name": "无尽庭审", "type": "SPELL", "cost": 0,
        "text": "4回合后，庭审结束。",
    },
    # 第三轮「庭审时长」菜单实际下发的选项版 card_id（效果与上面三张相同）。
    CARD_ID_TRIAL_FAST_OPTION: {
        "name": "快速庭审", "type": "SPELL", "cost": 7,
        "text": "庭审会立即结束！",
    },
    CARD_ID_TRIAL_SLOW_OPTION: {
        "name": "艰涩庭审", "type": "SPELL", "cost": 4,
        "text": "庭审会在你的下个回合开始时结束。",
    },
    CARD_ID_TRIAL_ENDLESS_OPTION: {
        "name": "无尽庭审", "type": "SPELL", "cost": 0,
        "text": "庭审会在4回合后结束。",
    },

    # ------------------------------------------------------------ 2026-10-25 新增
    CARD_ID_GLACIER: {
        "name": "冰川裂片", "type": "MINION", "cost": 1,
        "attack": 2, "health": 1,
        "text": "战吼：冻结一个敌人。",
    },
    CARD_ID_GLACIER_LEGACY: {
        "name": "冰川裂片", "type": "MINION", "cost": 1,
        "attack": 2, "health": 1,
        "text": "战吼：冻结一个敌人。",
    },
    CARD_ID_SAVIS: {
        "name": "梦魇之王萨维斯", "type": "MINION", "cost": 4,
        "attack": 4, "health": 4,
        "text": "战吼：从你的牌库中发现一张随从牌。使其获得黑暗之赐。",
    },
    CARD_ID_KISHIX: {
        "name": "基希克斯", "type": "MINION", "cost": 6,
        "attack": 6, "health": 6,
        "text": "战吼：在本局对战中，如果你完成过仪式，"
                "即可选择并开启一项罪孽仪式。",
    },
    CARD_ID_SIN_FAMINE: {
        "name": "饥荒仪式", "type": "SPELL", "cost": 3,
        "text": "2回合后，从你对手的牌库中抽五张牌。",
    },
    CARD_ID_SIN_DOMINION: {
        "name": "支配仪式", "type": "SPELL", "cost": 3,
        "text": "2回合后，随机召唤三个传说随从，并获得它们的1费复制。",
    },
    CARD_ID_SIN_CONQUEST: {
        "name": "征服仪式", "type": "SPELL", "cost": 3,
        "text": "2回合后，使你各处的随从获得+6/+6。",
    },

    # ------------------------------------------------------------ 2026-10-25 四古神
    # 四张牌本体只登记身材/费用，评分沿用通用随从逻辑（用户确认不做专属特调）。
    # 技能子卡 BE_009t1~t4 / BE_017t3~t6 / BE_784t1~t4 永远不进日志，故意不入表。
    CARD_ID_CUTHUN: {
        "name": "克苏恩，帝国之眼", "type": "MINION", "cost": 10,
        "attack": 12, "health": 12,
        "text": "古神。如果你在本局对战中触发了克苏恩的全部4个技能，本随从的技能效果变为三倍。",
    },
    CARD_ID_YOGG_SARON: {
        "name": "古神主谋尤格-萨隆", "type": "MINION", "cost": 9,
        "attack": 7, "health": 5,
        "text": "古神 战吼：结束你的回合。尤格-萨隆为你进行一个额外回合。（每局对战限一次）",
    },
    CARD_ID_NZOTH: {
        "name": "恩佐斯，梦境之末", "type": "MINION", "cost": 15,
        "attack": 5, "health": 7,
        "text": "古神 在本局对战中，你每召唤过一个亡语随从，本牌的法力值消耗便减少（1）点。",
    },
    CARD_ID_YSHARAJ: {
        "name": "亚煞极，泰坦杀神", "type": "MINION", "cost": 10,
        "attack": 10, "health": 10,
        "text": "古神 战吼：如果你在本局对战中消耗了30点法力值用于随从牌上，获得免疫1回合。",
    },
}


# ---------------------------------------------------------------- 查询辅助
def card_item(card_id) -> dict:
    """card_id -> 卡牌属性字典；人工表优先，缺失时回退 cards.json。"""
    key = str(card_id or "")
    item = CARD_ATTRIBUTES.get(key)
    if item is not None:
        return dict(item)
    if not key:
        return {}
    try:
        from json_op import query_json_item
        return query_json_item(key) or {}
    except Exception:
        return {}


def card_text(card_id) -> str:
    """card_id -> 卡面文本；查不到返回空串（search 的目标推断用）。"""
    return card_item(card_id).get("text") or ""


def card_name(card_id) -> str:
    """card_id -> 中文卡名；查不到返回空串。"""
    return card_item(card_id).get("name") or ""


def item_stats(card_id) -> tuple:
    """card_id -> (攻击力, 生命值)；人工表优先。"""
    item = card_item(card_id)
    try:
        return int(item.get("attack", 0) or 0), int(item.get("health", 0) or 0)
    except (TypeError, ValueError):
        return 0, 0


def item_flag(card_id, flag: str) -> int:
    """card_id -> 关键字标记（嘲讽/复生/突袭…）。"""
    try:
        return int(card_item(card_id).get(flag, 0) or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- 卡组上下文
class DwysDeckContext(DeckContext):
    """动物园术的对局进度：撕裂、兆示、挂起仪式、庭审。"""

    def __init__(self, tears_in_deck=0, tears_drawn=0, herald_count=0,
                 ritual_pending=0, summon_ritual_pending=0,
                 trial_pending=False, trial_effects=(), trial_due_turn=0,
                 dragon_played=False,
                 ritual_completed=False):
        self.tears_in_deck = max(0, int(tears_in_deck or 0))
        self.tears_drawn = max(0, int(tears_drawn or 0))
        self.herald_count = max(0, int(herald_count or 0))
        self.ritual_pending = max(0, int(ritual_pending or 0))
        self.summon_ritual_pending = max(0, int(summon_ritual_pending or 0))
        self.trial_pending = bool(trial_pending)
        self.trial_effects = tuple(trial_effects or ())
        self.trial_due_turn = int(trial_due_turn or 0)
        self.dragon_played = bool(dragon_played)
        # 基希克斯 BE_090 的战吼前置：本局是否已有仪式结算完成。
        self.ritual_completed = bool(ritual_completed)

    @classmethod
    def from_state(cls, state):
        """真实对局重建：只依赖 StrategyState 的坟场/场面公开数据。

        牌库内容不可见，撕裂数量用“已洗入 - 已抽到/已消耗”近似：
          洗入源：续连熵能/暮光时空跃迁者各 2 张；
          消耗源：抽到并自伤的 TIME_025t、以及灾毁迅疾幼龙战吼吃掉的 1 张。

        仪式完成判定同样只看公开痕迹：
          * 克苏恩仪式 BE_726 结算 -> 四个技能 BE_013t1~t4（都是法术）进坟场；
          * 召唤仪式 BE_085 结算 -> 克拉西斯 BE_085t 出现在场面/坟场/死亡表；
          * 已开启罪孽仪式（BE_090t1~t3 进坟场）说明前置条件曾满足。
        """
        if state is None:
            return cls()
        graveyard = list(getattr(state, "my_graveyard", ()) or ())
        board = list(getattr(state, "my_minions", ()) or ())
        dead_ids = set(getattr(state, "my_dead_card_ids", ()) or ())
        gy = Counter(str(getattr(entity, "card_id", "") or "")
                     for entity in graveyard)
        bd = Counter(str(getattr(entity, "card_id", "") or "")
                     for entity in board)

        tears_drawn = gy[CARD_ID_TEAR]
        drakes = gy[CARD_ID_DRAKE] + bd[CARD_ID_DRAKE]
        tears_in_deck = max(
            0,
            2 * (gy[CARD_ID_ENTROPY] + gy[CARD_ID_TIMEWARP])
            - tears_drawn - drakes,
        )
        herald_count = max(
            gy[CARD_ID_HERALD] + bd[CARD_ID_HERALD],
            sum(1 for card_id in dead_ids if card_id == CARD_ID_HERALD),
        )
        ritual_completed = bool(
            gy[CARD_ID_CUTHUN_FURY] + gy[CARD_ID_CUTHUN_DOMAIN]
            + gy[CARD_ID_CUTHUN_GAZE] + gy[CARD_ID_CUTHUN_SERVANT]
            or gy[CARD_ID_KRATHESI] + bd[CARD_ID_KRATHESI]
            or CARD_ID_KRATHESI in dead_ids
            or gy[CARD_ID_SIN_FAMINE] + gy[CARD_ID_SIN_DOMINION]
            + gy[CARD_ID_SIN_CONQUEST]
        )
        return cls(
            tears_in_deck=tears_in_deck,
            tears_drawn=tears_drawn,
            herald_count=herald_count,
            dragon_played=drakes > 0,
            ritual_completed=ritual_completed,
        )

    # ------------------------------------------------------------ 搜索钩子
    def on_card_played(self, card):
        """搜索模拟打出牌后推进进度（在深拷贝分支上调用）。"""
        card_id = str(getattr(card, "card_id", "") or "")
        if card_id in (CARD_ID_ENTROPY, CARD_ID_TIMEWARP):
            self.tears_in_deck += 2
        elif card_id == CARD_ID_DRAKE:
            self.tears_in_deck = max(0, self.tears_in_deck - 1)
            self.dragon_played = True
        elif card_id == CARD_ID_HERALD:
            self.herald_count += 1
        elif card_id == CARD_ID_CUTHUN_RITUAL:
            self.ritual_pending += 1
        elif card_id == CARD_ID_SUMMON_RITUAL:
            self.summon_ritual_pending += 1
        elif card_id == CARD_ID_KAZAKUS:
            self.trial_pending = True

    def on_minion_died(self, minion):
        """本卡组不需要死亡计数；保留空实现以符合钩子约定。"""
