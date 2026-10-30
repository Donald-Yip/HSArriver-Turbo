# 快任务牧（kuaimu）专属的卡组上下文：继承 search.ai_state.DeckContext。
#
# 记录搜索/评分需要的"场面之外"的对局进度（对应 ai/kuaimu.txt）：
#   * 任务「寻求平衡」进度：已施放几个神圣法术 / 几个暗影法术（各需 4 个）；
#   * 本局死亡过的"复生随从"数量（雷斯·范盖斯特的复活池）。
#
# 数据来源与口径：
#   * 真实对局：神圣/暗影法术进度从 StrategyState.my_graveyard 统计；
#     复生随从死亡从 my_dead_card_ids（己方随从 PLAY -> GRAVEYARD）与
#     坟场实体的 REBORN 标签统计。
#   * 搜索模拟：doAction() 的钩子（on_card_played / on_minion_died）在
#     copy() 出来的分支上下文上推进进度，各分支互不污染。
#
# 认牌一律用 card_id，卡名只用于日志/注释（与用户要求一致）：
# 按 card_id 查本文件人工维护的 type / school / text 属性。新版本 4 张新卡
# 不在旧 cards.json 里，所以这里必须手工收录，否则任务进度会漏算。
from search.ai_state import DeckContext

# 任务「寻求平衡」(TLC_817)：施放 4 个神圣法术 -> 奖励生命之息(半身)；
# 施放 4 个暗影法术 -> 奖励死亡之触(半身)。两半身在手中拼合成循环新生。
QUEST_HOLY_REQUIRED = 4
QUEST_SHADOW_REQUIRED = 4

# 关键随从 card_id。
CARD_ID_QUEST = "TLC_817"            # 寻求平衡
CARD_ID_SOULTRONUS = "TLC_817t5"     # 索利托斯，循环新生
CARD_ID_LIFE_HALF = "TLC_817t3"      # 索利托斯，生命之息
CARD_ID_DEATH_HALF = "TLC_817t4"     # 索利托斯，死亡之触

# 卡组会用到的牌由卡组策略维护属性，避免评分依赖 cards.json 的文本/学派。
# text 只保留目标推断和关键字判断需要的人工规则信息；新卡（BE_*）必须在这里
# 有准确文本，否则 search._requires_target 会把需要目标的牌当成无目标。
CARD_ATTRIBUTES = {
    # ---------------------------------------------------------------- 1费
    "BE_223": {"name": "古言术：笞", "type": "SPELL", "school": "SHADOW",
               "text": "随机对一个敌人造成3点伤害。你使用的下一张暗影法术牌会重复此效果。"},
    "TLC_817": {"name": "寻求平衡", "type": "SPELL", "school": None,
                "text": "任务：施放4个神圣法术，奖励生命之息。"
                        "任务：施放4个暗影法术，奖励死亡之触。"},
    "CATA_302": {"name": "愈合", "type": "SPELL", "school": "HOLY",
                 "text": "为一个随从恢复所有生命值。抽一张牌。", "targeted": True},
    "BE_220": {"name": "熵能裂隙", "type": "SPELL", "school": "SHADOW",
               "text": "随机获取两张暗影法术牌。"},
    "CORE_CS2_004": {"name": "真言术：盾", "type": "SPELL", "school": "HOLY",
                     "text": "使一个随从获得+2生命值。抽一张牌。",
                     "targeted": True},
    "CS2_004": {"name": "真言术：盾", "type": "SPELL", "school": "HOLY",
                "text": "使一个随从获得+2生命值。抽一张牌。", "targeted": True},
    "CS2_004_Puzzle": {"name": "真言术：盾", "type": "SPELL", "school": "HOLY",
                       "text": "使一个随从获得+2生命值。抽一张牌。",
                       "targeted": True},
    "VAN_CS2_004": {"name": "真言术：盾", "type": "SPELL", "school": "HOLY",
                    "text": "使一个随从获得+2生命值。抽一张牌。",
                    "targeted": True},
    "BE_228": {"name": "祖拉玛特的仆从", "type": "MINION", "school": None,
               "text": "战吼：沉默一个随从。", "targeted": True},

    # ---------------------------------------------------------------- 2费
    "EDR_463": {"name": "暮光侵扰", "type": "SPELL", "school": "SHADOW",
                "text": "抉择：消灭一个攻击力小于或等于3的随从；"
                        "或者随机召唤一个法力值消耗为（2）的随从。"},
    "EDR_463a": {"name": "操纵荆棘", "type": "SPELL", "school": "SHADOW",
                 "text": "消灭一个攻击力小于或等于3的随从。", "targeted": True},
    "EDR_463b": {"name": "控制藤蔓", "type": "SPELL", "school": "SHADOW",
                 "text": "随机召唤一个法力值消耗为（2）的随从。"},
    "DINO_426": {"name": "生命仪式", "type": "SPELL", "school": "HOLY",
                 "text": "发现一张法力值消耗为（3）的随从牌，"
                         "召唤一个它的2/3的复制。"},
    "JAIL_941": {"name": "神圣之拥", "type": "SPELL", "school": "HOLY",
                 "text": "恢复4点生命值。获取一张能造成4点伤害的黑暗之拥。",
                 "targeted": True},
    "JAIL_941t": {"name": "黑暗之拥", "type": "SPELL", "school": "SHADOW",
                  "text": "造成4点伤害。", "targeted": True},

    # ---------------------------------------------------------------- 3费
    "BE_221": {"name": "古言术：预", "type": "SPELL", "school": "SHADOW",
               "text": "抽一张牌，并使其法力值消耗减少2点。"
                       "你使用的下一张暗影法术牌会重复此效果。"},
    "CAP_801": {"name": "幽魂不散", "type": "SPELL", "school": "SHADOW",
                "text": "使一个随从获得+2/+3，复生和嘲讽。", "targeted": True},
    "TIME_433": {"name": "抹除存在", "type": "SPELL", "school": None,
                 "text": "回溯。沉默并消灭一个随机敌方随从。"},
    "CAP_804": {"name": "捉鬼专家", "type": "MINION", "school": None,
                "text": "战吼：使一个友方随从获得复生。"
                        "如果其已经拥有复生，召唤一个它的复制。",
                "targeted": True},

    # ---------------------------------------------------------------- 4费
    "TLC_816": {"name": "墓晨太阳花", "type": "SPELL", "school": "HOLY",
                "text": "抽两张牌。延系：本牌法力值消耗减少2点。"},
    "CAP_805": {"name": "灵质处决", "type": "SPELL", "school": "SHADOW",
                "text": "消灭所有随从。每个玩家各获取一张法力值消耗为3的"
                        "法术牌，该法术可以重新召唤其被消灭的随从。"},

    # ---------------------------------------------------------------- 5费
    "CATA_308": {"name": "麦迪文的胜利", "type": "SPELL", "school": "HOLY",
                 "text": "对所有随从造成4点伤害。如果你控制着传说牌，"
                         "本牌的法力值消耗为1点。"},

    # ---------------------------------------------------------------- 7费
    "CAP_806": {"name": "雷斯·范盖斯特", "type": "MINION", "school": None,
                "text": "战吼：复活在本局对战中你的复生过的随从，"
                        "并使其攻击随机敌方随从。"},

    # ---------------------------------------------------------------- 任务奖励
    "TLC_817t5": {"name": "索利托斯，循环新生", "type": "MINION",
                  "school": None,
                  "text": "嘲讽。复生。战吼：召唤一个本随从的复制。"
                          "亡语：随机对一个敌人造成5点伤害。"},
    "TLC_817t3": {"name": "索利托斯，生命之息", "type": "MINION",
                  "school": None,
                  "text": "嘲讽。战吼：召唤一个本随从的复制。"
                          "如果你手中有索利托斯的两部分，将其拼合！"},
    "TLC_817t4": {"name": "索利托斯，死亡之触", "type": "MINION",
                  "school": None,
                  "text": "复生。亡语：随机对一个敌人造成5点伤害。"
                          "如果你手中有索利托斯的两部分，将其拼合！"},
}


def card_item(card_id):
    """card_id -> 策略人工维护的属性；认不出时回退 cards.json，再认不出返回空 dict。

    回退很重要：熵能裂隙会随机给暗影法术，那些牌不在本卡组的人工表里，
    但任务进度仍必须按 cards.json 的 spellSchool 统计。
    """
    item = CARD_ATTRIBUTES.get(card_id)
    if item:
        return item
    try:
        from json_op import query_json_item
        data = query_json_item(card_id) or {}
    except Exception:
        return {}
    if not data:
        return {}
    return {
        "name": data.get("name"),
        "type": data.get("type") or data.get("cardtype"),
        "school": data.get("spellSchool"),
        "text": data.get("text") or "",
    }


def spell_school(card_id):
    """card_id -> 法术学派（HOLY / SHADOW…）；不是法术或认不出返回 None。"""
    return card_item(card_id).get("school")


def card_text(card_id):
    """card_id -> 卡牌描述文本（目标推断/发现评分用）；认不出返回空串。"""
    return card_item(card_id).get("text") or ""


def _entity_reborn(entity):
    """坟场实体是否带复生（优先读属性，其次读 Power.log 的 REBORN 标签）。"""
    reborn = getattr(entity, "reborn", None)
    if reborn is not None:
        return bool(reborn)
    try:
        return int(entity.query_tag("REBORN")) > 0
    except Exception:
        return False


class KuaimuDeckContext(DeckContext):
    """快牧上下文：任务进度 + 复生随从死亡计数。"""

    def __init__(self, holy_spells_cast=0, shadow_spells_cast=0,
                 reborn_minion_deaths=0, soultronus_died=False):
        self.holy_spells_cast = int(holy_spells_cast or 0)
        self.shadow_spells_cast = int(shadow_spells_cast or 0)
        # 本局死亡过的"复生随从"数量（循环新生、死亡之触半身、被幽魂不散
        # /捉鬼专家贴过复生的随从）。雷斯·范盖斯特的复活池。
        self.reborn_minion_deaths = int(reborn_minion_deaths or 0)
        self.soultronus_died = bool(soultronus_died)

    # ---------------------------------------------------------------- 构造
    @classmethod
    def from_graveyard(cls, graveyard, dead_card_ids=None):
        """从坟场与 Power.log 死亡历史重建上下文。

        `dead_card_ids` 来自己方随从 PLAY -> GRAVEYARD 的实际转换；有该记录时
        用它判定关键随从死亡，避免把弃置等仅进入坟场的情况当作死亡。
        """
        context = cls()
        dead_card_ids = (None if dead_card_ids is None
                         else set(dead_card_ids))
        for entity in graveyard or ():
            card_id = getattr(entity, "card_id", None)
            item = card_item(card_id)
            if item.get("type") == "SPELL":
                school = item.get("school")
                if school == "HOLY":
                    context.holy_spells_cast += 1
                elif school == "SHADOW":
                    context.shadow_spells_cast += 1
            if _entity_reborn(entity):
                context.reborn_minion_deaths += 1
        if dead_card_ids is None:
            dead_card_ids = {
                getattr(entity, "card_id", None) for entity in (graveyard or ())}
        context.soultronus_died = CARD_ID_SOULTRONUS in dead_card_ids
        # 兜底：死亡记录里有循环新生，但坟场实体没带 REBORN 标签时仍算一个。
        if context.soultronus_died and context.reborn_minion_deaths == 0:
            context.reborn_minion_deaths = 1
        return context

    # ---------------------------------------------------------------- 模拟钩子
    def on_card_played(self, card):
        """doAction 模拟出牌回调：法术按学派累计任务进度。"""
        item = card_item(getattr(card, "card_id", None))
        if item.get("type") != "SPELL":
            return
        school = item.get("school")
        if school == "HOLY":
            self.holy_spells_cast += 1
        elif school == "SHADOW":
            self.shadow_spells_cast += 1

    def on_minion_died(self, minion):
        """doAction 模拟中我方随从死亡回调：累计复生随从死亡数。"""
        card_id = getattr(minion, "card_id", None)
        if card_id == CARD_ID_SOULTRONUS:
            self.soultronus_died = True
        if (getattr(minion, "reborn", 0)
                or card_id in (CARD_ID_SOULTRONUS, CARD_ID_DEATH_HALF)):
            self.reborn_minion_deaths += 1

    # ---------------------------------------------------------------- 查询
    def holy_left(self):
        """神圣法术任务还差几个。"""
        return max(0, QUEST_HOLY_REQUIRED - self.holy_spells_cast)

    def shadow_left(self):
        """暗影法术任务还差几个。"""
        return max(0, QUEST_SHADOW_REQUIRED - self.shadow_spells_cast)

    def holy_quest_open(self):
        return self.holy_left() > 0

    def shadow_quest_open(self):
        return self.shadow_left() > 0

    def quest_complete(self):
        """两个任务（4神圣+4暗影）是否都完成。"""
        return not self.holy_quest_open() and not self.shadow_quest_open()

    def reborn_death_count(self):
        """死过的复生随从数量（雷斯·范盖斯特评分用）。"""
        return max(0, self.reborn_minion_deaths)

    def summary(self):
        return (f"神圣{self.holy_spells_cast}/{QUEST_HOLY_REQUIRED} "
                f"暗影{self.shadow_spells_cast}/{QUEST_SHADOW_REQUIRED} "
                f"复生死亡={self.reborn_minion_deaths} "
                f"循环新生死亡={self.soultronus_died}")

    def __repr__(self):
        return f"KuaimuDeckContext({self.summary()})"
