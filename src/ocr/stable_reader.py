"""Require a stable, high-confidence recommendation before parsing."""

import time

from src.ocr.preprocess import iter_preprocess_recommendation


class OcrRejectedError(RuntimeError):
    """本次读取没拿到可用的稳定证据。

    ``attempts`` 记录每一帧的 OCR 结果（``(text, confidence)`` 元组；``None``
    表示这一帧压根没读出具信度的文本），供调用方把"到底读到了什么"打出来。
    用户反馈里的 `recommendation_not_stable` 只有一句码，没有原文根本分不清
    是"面板没对准"还是"面板还在刷新"。
    """

    def __init__(self, message, attempts=()):
        super().__init__(message)
        self.attempts = tuple(attempts)


class StableRecommendationReader:
    def __init__(
        self,
        config,
        backend,
        sleep=time.sleep,
        text_normalizer=None,
        required_headers=(),
        cache=None,
        stability_key=None,
    ):
        self.config = config
        self.backend = backend
        self.sleep = sleep
        self.text_normalizer = text_normalizer or self._normalize
        self.required_headers = tuple(required_headers)
        # 可选：把一帧证据映射成“结论指纹”（可哈希）。给了它，稳定判定就按
        # 结论而不是 OCR 原文比 —— 同一句留牌建议被 OCR 读成多行/少一行、
        # 或面板标题行时有时无，只要结论一致就算稳（换牌用：见 FSM_action）。
        self.stability_key = stability_key
        # Panel-pixel -> OCR evidence cache. Identical pixels always produce
        # identical OCR output, so a frame whose exact hash was already
        # recognized can skip the (expensive) inference entirely.
        self._cache = {} if cache is None else cache
        # 最近一帧“读到了但没过门槛”的证据（只用于失败诊断）。
        self._last_rejected = None

    def _cached(self, frame):
        """Reuse the OCR result for an unchanged panel, or None on miss."""
        frame_hash = getattr(frame, "exact_hash", None)
        if not frame_hash:
            return None
        evidence = self._cache.get(frame_hash)
        if evidence is None:
            return None
        return type(evidence)(
            frame.frame_id,
            time.time(),
            evidence.lines,
            evidence.normalized_text,
            evidence.confidence,
            evidence.backend,
            evidence.preprocessing,
        )

    def read(self, frame_supplier, roi_supplier):
        previous_key = None
        stable_count = 0
        # 本次读取里每个“结论指纹”出现过几次：容忍 A/B/A 这种抖动。
        # 以前只认“连续两帧完全相同”，OCR 在两行文字之间来回跳一次就整轮
        # 判失败（用户反馈「换牌经常 not stable」）。
        seen_counts = {}
        attempts = []
        for attempt in range(self.config.max_attempts):
            frame = frame_supplier()
            roi = roi_supplier(frame)
            evidence = self._cached(frame)
            if evidence is None:
                evidence = self._recognize_roi(
                    roi, frame.frame_id, self._client_upscale(frame)
                )
                frame_hash = getattr(frame, "exact_hash", None)
                if evidence is not None and frame_hash:
                    self._cache[frame_hash] = evidence
            if evidence is None:
                # 这一帧没给出合格证据：把“读到了但不够格”的原文也记下来，
                # 失败信息才能区分“没读到字”和“字读到了但置信度低”。
                rejected = self._last_rejected
                attempts.append(
                    (rejected.normalized_text, rejected.confidence)
                    if rejected is not None and rejected.normalized_text
                    else None)
                previous_key = None
                stable_count = 0
                if attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_interval_seconds)
                continue
            text = evidence.normalized_text
            key = self._stability_key(evidence, text)
            if (evidence.confidence >= self.config.min_ocr_confidence
                    and text and key is not None):
                attempts.append((text, evidence.confidence))
                if key == previous_key:
                    stable_count += 1
                else:
                    stable_count = 1
                    previous_key = key
                seen_counts[key] = seen_counts.get(key, 0) + 1
                # 连续 stable_frames 帧相同 → 稳；或者同一结论在本次读取里
                # 已经出现过 stable_frames 次、而**最后一帧就是它** → 也接受
                # （返回的仍是最后一帧，frame_id 与调用方手里的当前帧一致）。
                if (stable_count >= self.config.stable_frames
                        or seen_counts[key] >= self.config.stable_frames):
                    return evidence
            else:
                # 读不清/空文本/给不出结论的帧：记下原文方便排错，但不计入稳定。
                attempts.append((text, evidence.confidence))
                previous_key = None
                stable_count = 0
            if attempt + 1 < self.config.max_attempts:
                self.sleep(self.config.retry_interval_seconds)
        raise OcrRejectedError("recommendation_not_stable", attempts)

    def _stability_key(self, evidence, text):
        """这一帧的“结论指纹”：默认就是归一化文本。

        指纹必须可哈希（要拿去计数）；调用方给错类型时按“这一帧给不出结论”
        处理，绝不让一个辅助判定把整条换牌流程抛崩。
        """
        if self.stability_key is None:
            return text
        try:
            key = self.stability_key(evidence)
            hash(key)
            return key
        except Exception:
            return None

    def read_frame(self, frame, roi_supplier):
        evidence = self._cached(frame)
        if evidence is None:
            evidence = self._recognize_roi(
                roi_supplier(frame), frame.frame_id, self._client_upscale(frame)
            )
            frame_hash = getattr(frame, "exact_hash", None)
            if evidence is not None and frame_hash:
                self._cache[frame_hash] = evidence
        if evidence is None:
            raise OcrRejectedError("recommendation_not_confident")
        return evidence

    def _client_upscale(self, frame):
        """客户区比基准 1920×1080 小时，按比例把 OCR 放大倍数补水回去。

        ROI 抓图时按客户区缩放（见 desktop_capture._screen_roi_box），客户区越小
        面板像素越少；这里用 max(ref_w/w, ref_h/h) 把损失的字号补回来，上限由
        config.ocr_small_window_max_upscale 限制（防止小窗口下 OCR 变慢拖崩超时）。
        客户区信息缺失或本来就 ≥ 基准时返回 1.0（行为不变）。
        """
        client = getattr(frame, "client_size", None)
        if not client:
            return 1.0
        try:
            width, height = int(client[0]), int(client[1])
        except (TypeError, ValueError, IndexError):
            return 1.0
        if width <= 0 or height <= 0:
            return 1.0
        from display_mapping import REFERENCE_CLIENT

        factor = max(REFERENCE_CLIENT[0] / width, REFERENCE_CLIENT[1] / height)
        if factor <= 1.0:
            return 1.0
        cap = float(getattr(self.config, "ocr_small_window_max_upscale", 2.0))
        return min(factor, cap)

    def _recognize_roi(self, roi, frame_id, scale: float = 1.0):
        # 留一份“最像样但没过门槛”的证据：全都不达标时至少能说清读到了什么
        # （置信度不够 vs 完全没字），排错时比只有 not_stable 一句码有用。
        best = None
        for candidate_name, candidate in iter_preprocess_recommendation(roi, scale):
            evidence = self.backend.recognize(candidate, frame_id, candidate_name)
            evidence = self._action_evidence(evidence)
            if best is None or evidence.confidence > best.confidence:
                best = evidence
            if (
                evidence.confidence >= self.config.min_ocr_confidence
                and evidence.normalized_text
            ):
                self._last_rejected = None
                return evidence
        self._last_rejected = best
        return None

    def _action_evidence(self, evidence):
        if self.required_headers:
            header_lines = [
                line
                for line in evidence.lines
                if line.text.strip() in self.required_headers
            ]
            if (
                not header_lines
                or max(line.confidence for line in header_lines)
                < self.config.min_ocr_confidence
            ):
                return type(evidence)(
                    evidence.frame_id,
                    evidence.created_at,
                    (),
                    "",
                    0.0,
                    evidence.backend,
                    evidence.preprocessing,
                )
        normalized_text = self.text_normalizer(evidence.normalized_text)
        expected_lines = normalized_text.splitlines()
        relevant_lines = []
        expected_index = 0
        for line in evidence.lines:
            normalized_line = self.text_normalizer(line.text)
            parts = normalized_line.splitlines()
            # A card name is retained only after the contextual '选择卡牌'
            # marker; normalizing that OCR line alone deliberately drops it.
            if (
                not parts
                and expected_index > 0
                and expected_lines[expected_index - 1] == "选择卡牌"
            ):
                parts = self._normalize(line.text).splitlines()
            if not parts:
                continue
            if expected_lines[expected_index : expected_index + len(parts)] == parts:
                relevant_lines.append(line)
                expected_index += len(parts)
        relevant_lines = tuple(relevant_lines)
        confidence = (
            min(line.confidence for line in relevant_lines)
            if relevant_lines
            else evidence.confidence
        )
        return type(evidence)(
            evidence.frame_id,
            evidence.created_at,
            relevant_lines,
            normalized_text,
            confidence,
            evidence.backend,
            evidence.preprocessing,
        )

    @staticmethod
    def _normalize(text):
        return "\n".join(
            line.strip() for line in (text or "").splitlines() if line.strip()
        )
