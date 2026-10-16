"""Per-shot material planning for library sources.

分镜对齐的关键实现：把「文案分镜 + 字幕时间轴 + 素材库」三者合成一份
**逐镜头画面计划**（`ClipPlan`），交给 `video.combine_videos` 按计划切片。

为什么不能"整库顺序拼接"：

* 旁白念到第 4 句时，画面可能还是第 1 条素材——画面与文案无关；
* 素材总时长不够时，同一条 5 秒片段会被 `itertools.cycle` 反复播放
  （实测 69 秒成片里同一条素材原样出现 4 次）。

计划按"镜头"分配而不是按"文件"分配：

1. 每个镜头用字幕时间轴算出它在成片里应该占的时长（无字幕时按镜头数均分）；
2. 镜头 term 与库内标签做打分，命中的候选优先，且优先**没用过**的、
   以及与已用素材标签重合度低的（避免同风格的画面反复出现）；
3. 素材不够长时，一条镜头可以吃多条不同素材补满时长；
4. 全部分镜排完仍差时长时，用还没用过的素材补尾（每条只用一次）；
5. 完全没有匹配素材的镜头，登记为拍摄缺口（交给 `shoot_needs`），
   同时用兜底素材填上，保证不会出现"旁白有话、画面空白"。
"""

import math
import os
import re
from dataclasses import dataclass, field

from loguru import logger

# 标签子串匹配的最短长度：单字标签（"美"）几乎命中所有素材，宁可不匹配。
_MIN_TAG_MATCH_LENGTH = 2
# 两条素材的标签互相包含度（交集/较小集）超过此值视为"同一视觉家族"，
# 同一条成片里优先只出一家，避免三张几乎一样的美甲特写轮流播。
_FAMILY_SIMILARITY_LIMIT = 0.6
# 排序扣分：同视觉家族扣得比"精确命中 vs 包含匹配"的分差更大，保证同款会让位。
_FAMILY_OVERLAP_PENALTY = 1.5
# 按家族累计时长再扣一次：同款照片会连着好几张，只扣"出现过"挡不住第二张。
_FAMILY_SECOND_PENALTY = 0.5
_REUSE_PENALTY = 0.5
# 单条素材剩余时长低于此值就不再复用（切出来的碎片没有观感）。
_MIN_SOURCE_REMAINDER_SECONDS = 0.4
# 镜头尾巴不足这个长度时，拉长本镜头最后一段而不是再切一个碎片出来。
_MIN_SHOT_CLIP_SECONDS = 1.5
# 每个镜头最多吃几条素材，防止一个长镜头把整库抽干。
_DEFAULT_MAX_CLIPS_PER_SHOT = 3
# 素材总量不够铺满所有镜头时，同一条素材最多被完整走几遍。
_MAX_SOURCE_PASSES = 2
# 计划总时长相对"音频 + 安全余量"的容忍度。
_DURATION_TOLERANCE_SECONDS = 0.5

_PUNCTUATION = re.compile(
    r"[\s，。！？、；：,.!?;:\-—–…·\"'“”‘’()（）\[\]【】<>《》/\\|~`@#$%^&*+=_]+"
)
_SRT_TIME = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})"
)
_ZOOM_SUFFIX = re.compile(r"\.zoom-[0-9a-f]+\.mp4$", re.IGNORECASE)


@dataclass(frozen=True)
class MaterialCandidate:
    """一条可被 combine_videos 使用的素材（图片已替换为渲染出的 zoom 片段）。"""

    path: str
    name: str
    kind: str = "video"
    duration: float = 0.0
    tags: tuple[str, ...] = ()
    tags_en: tuple[str, ...] = ()

    @property
    def family(self) -> frozenset:
        return frozenset(normalize_text(tag) for tag in self.tags if tag)


@dataclass
class PlannedClip:
    """一条待渲染的片段：源文件的哪一段、成片里播多久、对应哪个镜头。"""

    file_path: str
    start_time: float
    end_time: float
    target_duration: float
    shot_index: int = -1
    term: str = ""
    off_term: bool = False
    recycled: bool = False

    def as_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "start_time": round(self.start_time, 3),
            "end_time": round(self.end_time, 3),
            "target_duration": round(self.target_duration, 3),
            "shot_index": self.shot_index,
            "term": self.term,
            "off_term": self.off_term,
            "recycled": self.recycled,
        }


@dataclass
class ClipPlan:
    clips: list[PlannedClip] = field(default_factory=list)
    unmatched_shots: list[dict] = field(default_factory=list)
    shortfall: float = 0.0
    required_duration: float = 0.0

    @property
    def planned_duration(self) -> float:
        return round(sum(clip.target_duration for clip in self.clips), 3)

    @property
    def material_paths(self) -> list[str]:
        return list(dict.fromkeys(clip.file_path for clip in self.clips))

    def as_dicts(self) -> list[dict]:
        return [clip.as_dict() for clip in self.clips]


def normalize_text(value: str) -> str:
    """去掉标点/空白并转小写，用于标签、term、字幕文本的宽松比对。"""

    return _PUNCTUATION.sub("", (value or "").lower())


def source_name_of(file_path: str) -> str:
    """还原库内原始文件名（图片素材会先渲染成 `<原名>.zoom-<hash>.mp4`）。"""

    name = os.path.basename(file_path or "")
    return _ZOOM_SUFFIX.sub("", name) or name


def parse_srt_cues(path: str) -> list[tuple[float, float, str]]:
    """解析 SRT 为 [(开始秒, 结束秒, 文本)]；文件缺失或损坏时返回空列表。"""

    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8-sig") as handle:
            content = handle.read()
    except OSError as exc:
        logger.warning(f"shot alignment cannot read subtitle: {path}, error={exc}")
        return []

    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", content.strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        time_line_index = next(
            (index for index, line in enumerate(lines) if "-->" in line), None
        )
        if time_line_index is None:
            continue
        stamps = _SRT_TIME.findall(lines[time_line_index])
        if len(stamps) < 2:
            continue
        text = "".join(lines[time_line_index + 1:]).strip()
        if not text:
            continue
        cues.append((_seconds(stamps[0]), _seconds(stamps[1]), text))
    return cues


def _seconds(match: tuple[str, str, str, str]) -> float:
    hours, minutes, seconds, millis = match
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def shot_windows(
    shots: list[dict],
    cues: list[tuple[float, float, str]],
    audio_duration: float,
) -> list[tuple[float, float]]:
    """每个镜头在成片时间轴上的 [开始, 结束) 区间。

    分镜 text 必须是文案原句（创作页提示词要求），所以可以用字幕时间轴反推：
    从上一镜头结束处开始，累积 cue 文本直到覆盖本镜头句子的 80%。字幕缺失
    或匹配不上时按镜头数均分，保证仍然有确定的时间预算。
    """

    total = max(0.0, float(audio_duration or 0.0))
    if not shots:
        return []
    if not cues or total <= 0:
        share = total / len(shots) if total > 0 else 0.0
        return [
            (round(index * share, 3), round((index + 1) * share, 3))
            for index in range(len(shots))
        ]

    windows: list[tuple[float, float]] = []
    position = 0  # 字幕游标：镜头按文案顺序向前吃掉 cue
    for shot in shots:
        needle = normalize_text(str(shot.get("text") or ""))
        needed = max(1, int(len(needle) * 0.8)) if needle else 1
        if position >= len(cues):
            break
        start = cues[position][0]
        collected = 0
        probe = position
        while probe < len(cues) and collected < needed:
            collected += len(normalize_text(cues[probe][2]))
            probe += 1
        end = cues[probe - 1][1] if probe > position else start
        if windows and start < windows[-1][1]:
            start = windows[-1][1]
        if end <= start:
            end = start
        windows.append((round(start, 3), round(end, 3)))
        position = probe

    # 字幕用尽（逐词字幕、镜头数多于 cue 数）时，剩余镜头均分剩余时间。
    remaining_shots = len(shots) - len(windows)
    if remaining_shots > 0:
        tail_start = windows[-1][1] if windows else float(cues[0][0])
        tail_share = max(0.0, total - tail_start) / remaining_shots
        for index in range(remaining_shots):
            start = tail_start + tail_share * index
            windows.append((round(start, 3), round(start + tail_share, 3)))

    # 最后一个镜头补到音频结束，避免尾部画面缺一段。
    if windows and total > windows[-1][1]:
        start = windows[-1][1]
        windows[-1] = (start, round(total, 3))
    return windows


def score_candidate(term: str, candidate: MaterialCandidate) -> float:
    """镜头 term 与素材标签的匹配分；0 表示不相关（宁缺毋滥）。"""

    needle = normalize_text(term)
    if not needle:
        return 0.0

    score = 0.0
    for tag in candidate.tags:
        normalized = normalize_text(tag)
        if not normalized:
            continue
        if normalized == needle:
            score = max(score, 3.0)
        elif (
            len(normalized) >= _MIN_TAG_MATCH_LENGTH
            and len(needle) >= _MIN_TAG_MATCH_LENGTH
            and (needle in normalized or normalized in needle)
        ):
            score = max(score, 2.0)
    for tag in candidate.tags_en:
        normalized = normalize_text(tag).replace(" ", "")
        if not normalized:
            continue
        if normalized == needle.replace(" ", ""):
            score = max(score, 2.5)
        elif (
            len(normalized) >= _MIN_TAG_MATCH_LENGTH
            and needle.replace(" ", "") in normalized
        ):
            score = max(score, 1.5)
    if len(needle) >= _MIN_TAG_MATCH_LENGTH and needle in normalize_text(candidate.name):
        score = max(score, 1.0)
    return score


def _family_overlap(candidate: MaterialCandidate, families: list[frozenset]) -> float:
    """与已用素材的最大标签重合度（1=完全同款，0=全新）。

    用了双向包含而不是 Jaccard：素材 A「美甲/法式美甲/手部特写」和素材 B
    「美甲/法式美甲/水晶美甲/金色美甲」的 Jaccard 只有 0.43，但两边的标签互相
    包含，用重叠系数（交集/较小集）算出来是 1.0——实测三张同款美甲特写就是
    这样被同时选进一条成片的。
    """

    candidate_family = candidate.family
    if not candidate_family:
        return 0.0
    worst = 0.0
    for family in families:
        intersection = len(candidate_family & family)
        if not intersection:
            continue
        worst = max(worst, intersection / min(len(candidate_family), len(family)))
    return worst


def _extend_last_clip(clip: PlannedClip, extra: float, speed: float) -> None:
    """把镜头尾部的一点点时长并进最后一段，而不是另切一个碎片段。"""

    if extra <= 0:
        return
    clip.end_time = round(clip.end_time + extra * speed, 3)
    clip.target_duration = round(clip.target_duration + extra, 3)


def _rank_candidates(
    candidates: list[MaterialCandidate],
    term: str,
    usage: dict[str, int],
    families: list[frozenset],
    family_seconds: dict[frozenset, float] | None = None,
) -> list[tuple[float, MaterialCandidate, bool]]:
    """候选排序：匹配分为主，同款视觉与已用素材扣分。

    扣分必须能翻盘，否则同款素材会一直霸占镜头：三条同款美甲特写和一个拍美甲
    过程的视频都算"美甲"命中，三张特写分更高（标签里正好含"美甲"），于是一条
    70 秒成片里连出三张几乎一样的画面。扣分有三项：

    * `_FAMILY_OVERLAP_PENALTY`：这个标签家族与已用素材的标签重合度；
    * `_FAMILY_SECOND_PENALTY`：该家族在成片里累计占用的秒数——光看重合度
      只能区分"这张/那张"，累计时长才压得住连续几张同款；
    * `_REUSE_PENALTY`：同一条具体素材每多用一次。

    前两项之和大于"精确命中 vs 包含匹配"的分差，所以同款会让位给风格不同但
    同样贴题的素材。

    返回 (匹配分, 素材, 是否兜底)。完全没有命中时也返回候选，但标记为兜底，
    调用方据此登记拍摄缺口——宁缺毋滥，但不留空白画面。
    """

    seconds = family_seconds or {}
    scored = [(score_candidate(term, candidate), candidate) for candidate in candidates]
    matching = [item for item in scored if item[0] > 0]
    off_term = not matching
    pool = matching or scored

    def rank_key(item):
        score, candidate = item
        overlap = _family_overlap(candidate, families)
        # 重合度是连续值而不是 0/1：三张同款照片里，用得越久的那张扣得越多，
        # 规划器才会换到"没那么重复"的那张或干脆换画面。
        return -(
            score
            - _FAMILY_OVERLAP_PENALTY * overlap
            - _FAMILY_SECOND_PENALTY * seconds.get(candidate.family, 0.0)
            - _REUSE_PENALTY * usage.get(candidate.name, 0)
        )

    pool.sort(key=rank_key)
    return [(score, candidate, off_term) for score, candidate in pool]


def build_clip_plan(
    *,
    shots: list[dict],
    candidates: list[MaterialCandidate],
    audio_duration: float,
    required_duration: float,
    max_clip_duration: float = 5.0,
    clip_speed: float = 1.0,
    cues: list[tuple[float, float, str]] | None = None,
    variant: int = 1,
    max_clips_per_shot: int = _DEFAULT_MAX_CLIPS_PER_SHOT,
) -> ClipPlan:
    """按分镜顺序产出逐镜头画面计划。

    `variant` 用于 video_count>1：第 2 条及以后换一套候选优先级，避免多条
    视频画面完全一致。
    """

    speed = max(0.1, float(clip_speed or 1.0))
    clip_cap = max(1.0, float(max_clip_duration or 5.0))
    required = max(0.0, float(required_duration or audio_duration or 0.0))
    plan = ClipPlan(required_duration=round(required, 3))
    if not shots or not candidates or required <= 0:
        return plan

    usable = [candidate for candidate in candidates if candidate.path and candidate.duration > 0]
    if not usable:
        return plan
    if variant > 1:
        offset = (variant - 1) % len(usable)
        usable = usable[offset:] + usable[:offset]

    windows = shot_windows(shots, cues or [], audio_duration or required)
    usage: dict[str, int] = {}
    consumed: dict[str, float] = {}
    wraps: dict[str, int] = {}
    families: list[frozenset] = []
    # 每个家族已占用的成片时长，用来在"同款"之间做时长配额。
    family_seconds: dict[frozenset, float] = {}

    def take(
        candidate: MaterialCandidate,
        shot_index: int,
        term: str,
        remaining: float,
        off_term: bool,
    ) -> PlannedClip | None:
        """从候选未使用的部分切出一段，返回 None 表示已无可用时长。

        素材不够铺满所有镜头时允许整条素材**再走一遍**（最多
        ``_MAX_SOURCE_PASSES`` 遍），而不是把同一个 5 秒片段无限循环：
        镜头顺序仍然对得上，重复也收敛在可预期范围内。
        """

        already = consumed.get(candidate.name, 0.0)
        recycled = False
        if candidate.duration - already < _MIN_SOURCE_REMAINDER_SECONDS:
            if wraps.get(candidate.name, 0) + 1 >= _MAX_SOURCE_PASSES:
                return None
            wraps[candidate.name] = wraps.get(candidate.name, 0) + 1
            already = 0.0
            recycled = True
        available = candidate.duration - already
        if available < _MIN_SOURCE_REMAINDER_SECONDS:
            return None
        take_seconds = min(remaining, available)
        start = already
        end = min(candidate.duration, already + take_seconds * speed)
        actual = (end - start) / speed
        if actual <= 0:
            return None
        consumed[candidate.name] = end
        usage[candidate.name] = usage.get(candidate.name, 0) + 1
        family = candidate.family
        if family:
            # 同款画面累计时长记账。库里的三张美甲特写同属一个家族：只看
            # "有没有用过" 只能挡住第一张，第二、第三张照样进片（实测 69 秒
            # 成片里连着播了三张几乎一样的图）。按家族计时长，某个家族占用
            # 越多，后续选它的排序分越低，直到换成别的画面。
            family_seconds[family] = family_seconds.get(family, 0.0) + actual
            if _family_overlap(candidate, families) < _FAMILY_SIMILARITY_LIMIT:
                families.append(family)
        return PlannedClip(
            file_path=candidate.path,
            start_time=round(start, 3),
            end_time=round(end, 3),
            target_duration=round(actual, 3),
            shot_index=shot_index,
            term=term,
            off_term=off_term,
            recycled=recycled,
        )

    for shot_index, (shot, window) in enumerate(zip(shots, windows)):
        start, end = window
        remaining = max(0.0, (end - start) or 0.0)
        if remaining <= 0:
            continue
        term = str(shot.get("term") or "")
        budget = min(remaining, max(0.0, required - plan.planned_duration))
        if budget <= 0:
            break

        placed = 0
        off_term_used = False
        cap = max(1, int(max_clips_per_shot))
        # 按轮次取片段：每轮过一遍候选。第一轮拿最佳素材的第一段，第二轮再拿
        # 同一条素材的下一段（或次优素材），这样一个长镜头才能被铺满，而不是
        # 只切出一个 5 秒片段就空着。
        while budget > 0.05 and placed < cap:
            if 0 < budget < _MIN_SHOT_CLIP_SECONDS and placed:
                # 镜头尾巴只剩零点几秒：拉长本镜头最后一段，而不是切出一个
                # 0.4 秒的碎片（碎片在成片里就是一次莫名其妙的抖动）。
                _extend_last_clip(plan.clips[-1], budget, speed)
                budget = 0.0
                break
            progressed = False
            for _score, candidate, off_term in _rank_candidates(
                usable, term, usage, families, family_seconds
            ):
                if budget <= 0.05 or placed >= cap:
                    break
                clip = take(candidate, shot_index, term, min(budget, clip_cap), off_term)
                if clip is None:
                    continue
                plan.clips.append(clip)
                budget -= clip.target_duration
                placed += 1
                progressed = True
                off_term_used = off_term_used or off_term
            if not progressed:
                break

        if 0 < budget < clip_cap and plan.clips:
            # 收尾：不足一条完整片段的零头并进本镜头最后一段。
            # 不这么做的话，镜头末尾会留下一个空洞——combine_videos 只能拿
            # 别的镜头用过的素材在结尾循环补上，听感上是"旁白还在讲，画面已经
            # 跳回前面了"。
            _extend_last_clip(plan.clips[-1], budget, speed)
            budget = 0.0

        if placed == 0 or off_term_used:
            plan.unmatched_shots.append(
                {
                    "term": term,
                    "title": str(shot.get("title") or term),
                    "brief": str(shot.get("brief") or ""),
                    "text": str(shot.get("text") or ""),
                    "off_term": off_term_used,
                }
            )

    # 分镜排完仍差时长：用还没用过的素材补尾，每条最多一次。
    for _ in range(2):
        remaining_budget = required - plan.planned_duration
        if remaining_budget <= _DURATION_TOLERANCE_SECONDS:
            break
        progressed = False
        for candidate in usable:
            if remaining_budget <= _DURATION_TOLERANCE_SECONDS:
                break
            if usage.get(candidate.name, 0):
                continue
            clip = take(candidate, -1, "", min(remaining_budget, clip_cap), True)
            if clip is None:
                continue
            plan.clips.append(clip)
            remaining_budget -= clip.target_duration
            progressed = True
        if not progressed:
            break

    # 只差安全余量（0.1s 量级）时延长最后一段，而不是再排一条片段——多出来的
    # 片段会让 combine_videos 的循环补齐再播一遍，画面在结尾突然重复。
    tail = required - plan.planned_duration
    if 0 < tail <= _DURATION_TOLERANCE_SECONDS and plan.clips:
        last = plan.clips[-1]
        last.end_time = round(last.end_time + tail * speed, 3)
        last.target_duration = round(last.target_duration + tail, 3)

    plan.shortfall = round(max(0.0, required - plan.planned_duration), 3)
    logger.info(
        f"shot alignment plan: {len(plan.clips)} clips covering "
        f"{plan.planned_duration:.1f}s of {required:.1f}s "
        f"(shots={len(shots)}, gaps={len(plan.unmatched_shots)}, "
        f"shortfall={plan.shortfall:.1f}s)"
    )
    return plan


def build_plan_inputs(
    *,
    shots: list[dict],
    candidates: list[MaterialCandidate],
    audio_duration: float,
    required_duration: float,
    max_clip_duration: float,
    clip_speed: float,
    subtitle_path: str = "",
) -> dict:
    """打包成可以在 generate_final_videos 里按 video_count 复用的输入。"""

    return {
        "shots": shots,
        "candidates": candidates,
        "audio_duration": audio_duration,
        "required_duration": required_duration,
        "max_clip_duration": max_clip_duration,
        "clip_speed": clip_speed,
        "cues": parse_srt_cues(subtitle_path),
    }


def estimated_repeat_count(plan: ClipPlan, max_clip_duration: float) -> int:
    """计划仍差多少条素材——用于在任务告警里如实告知用户。"""

    if plan.shortfall <= _DURATION_TOLERANCE_SECONDS:
        return 0
    clip = max(1.0, float(max_clip_duration or 5.0))
    return int(math.ceil(plan.shortfall / clip))