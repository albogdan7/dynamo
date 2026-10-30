class EvictionPolicy:
    def __init__(self):
        self.last_completion = {}
        self.last_gap = {}

    def on_pause(self, pause: dict):
        session = pause["session_id"]
        previous = self.last_completion.get(session)
        if previous is not None and pause["arrival_ms"] - previous >= 0:
            self.last_gap[session] = pause["arrival_ms"] - previous
        self.last_completion[session] = pause["now_ms"]
        gap = self.last_gap.get(session)
        return None if gap is None else pause["now_ms"] + gap
