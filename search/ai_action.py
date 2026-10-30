# AiAction类，仅供search.py使用，是ProposedAction一个简化版，去掉了很多不必要的属性和方法，只保留了搜索需要的部分。
#
# 为什么需要它：
#   ProposedAction 是"给执行层看的动作协议"：OCR 置信度、面板帧号 frame_id、
#   时间戳、指令原文、目标卡名、换牌槽位…… 搜索一个都不看。搜索每一步只关心
#   "做什么、对谁做、花多少费"，AiAction 就只留这些。
#
# 与旧结构的关系（三条约定）：
#   1. 字段名与 ProposedAction 对齐（action / source / target / destination /
#      cost / card_type / instruction）：卡组策略里的 rankActions 可以直接读
#      action.action、action.source.index，不用改一行。
#   2. source / target / destination 用轻量的 AiSlot（namedtuple，字段与
#      SlotRef 完全相同），搜索内部因此不用碰外部协议类；公开入口
#      Search.search() 再一一对应地转回 SlotRef。
#   3. card_id / entity_id 记的是"值"，card 记的是"那个实体对象"：
#        * card —— 本次动作用到的那张牌/那个随从（打出的手牌、发起攻击的随从、
#          使用的技能…），卡组策略直接 action.card.name 和中文卡名比大小，
#          识别关键牌（闪电风暴 / 鱼人领军 / 嗜血…），可读性远好于拿下标猜；
#        * card_id / entity_id —— 同一次动作的稳定"值"，跨模拟副本依然可靠。
#      注意：card 可能是模拟场面（doAction 的深拷贝）里的副本，**只能读它的
#      属性**（name / card_id / attack / health…），不要拿它去改场面，也不要
#      和真实场面的实体做 is 比较；模拟改动一律由 doAction 按下标在副本上做。
from collections import namedtuple
from dataclasses import dataclass, field

from src.recommendation_models import ActionKind


# 搜索里的"位置"引用：
#   kind  —— board_slot / hand / hero / hero_power / discover_slot
#   owner —— friendly / enemy（自己场上的英雄技能等可省）
#   index —— 1 基下标（场面号位 / 手牌号位 / 发现选项号位）
# 字段名与 src.recommendation_models.SlotRef 完全一致，转换就是逐字段搬运。
AiSlot = namedtuple("AiSlot", ["kind", "owner", "index"],
                    defaults=(None, None))


@dataclass(frozen=True)
class AiAction:
    """搜索用的精简动作：只描述"做什么、对谁做、花多少费"。

    frozen（不可变）：一个动作一旦生成就代表搜索树里的一条边，
    不允许在展开过程中被就地改写；同时天然可比较、可哈希，便于去重。
    """

    # 动作类型。沿用执行层的 ActionKind 枚举——它只是一个轻量枚举，不是
    # "复杂的协议类"，共用它可以和卡组策略（rankActions）零成本对齐。
    action: ActionKind
    # 人类可读说明；公开入口原样转成 ProposedAction 的指令文本。
    instruction: str = ""

    # 动作的发起位置：出牌是手牌号位，攻击是随从/英雄，技能是英雄技能……
    source: AiSlot | None = None
    # 动作的目标（攻击目标、指向性法术目标……），没有目标时为 None。
    target: AiSlot | None = None
    # 随从/地标的落点（第 i 个空隙，1 基），None = 执行层默认最右。
    # 由卡组策略的 play_destination(state, card) 决定（如暗誓信徒站位）。
    destination: AiSlot | None = None

    # 该动作消耗的法力（搜索用它做剩余法力剪枝）。
    cost: int = 0
    # 打出的卡牌类型（MINION / SPELL / WEAPON / LOCATION / HERO）。
    card_type: str | None = None

    # 抉择牌的选项名（如暮光侵扰的「操纵荆棘」/「控制藤蔓」）：执行层据此
    # 点对应选项；None = 普通牌。
    choice_name: str | None = None
    # 抉择选项子卡的 card_id（EDR_463a / EDR_463b）：卡组策略评分时按 card id
    # 认选项，不依赖中文名。
    choice_card_id: str | None = None

    # 相关卡牌的值信息（不是对象引用），方便按卡牌判断。
    card_id: str | None = None
    entity_id: str | None = None
    target_entity_id: str | None = None
    # 本次动作用到的实体对象（打出的手牌 / 攻击的随从 / 使用的技能…）。
    # 卡组策略用它取 .name 认关键牌；只读，且不参与相等性比较（同一个决策
    # 无论指向哪个副本对象都算同一个动作）。
    card: object | None = field(default=None, compare=False, repr=False)

    @classmethod
    def end_turn(cls) -> "AiAction":
        """没有任何可做动作时的兜底动作：结束回合。"""
        return cls(ActionKind.END_TURN, "结束回合")

    @property
    def card_name(self) -> str:
        """本次动作那张牌的中文名（取不到时返回空串，方便直接和卡名比）。"""
        return getattr(self.card, "name", "") or ""

    def __str__(self):
        return self.instruction or self.action.value
