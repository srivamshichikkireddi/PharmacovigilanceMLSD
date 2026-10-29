"""Minimal in-memory Redis covering exactly the commands pv.store uses."""
import time


class FakeRedis:
    def __init__(self): self.kv, self.lists, self.hashes, self.exp = {}, {}, {}, {}

    def _live(self, k):
        if k in self.exp and self.exp[k] <= time.time():
            self.kv.pop(k, None); self.exp.pop(k, None)
        return k in self.kv

    def ping(self): return True
    def get(self, k): return self.kv[k].encode() if self._live(k) else None
    def set(self, k, v, ex=None, nx=False):
        if nx and self._live(k): return None
        self.kv[k] = v if isinstance(v, str) else str(v)
        if ex: self.exp[k] = time.time() + ex
        return True
    def delete(self, k): self.kv.pop(k, None); return 1
    def exists(self, k): return 1 if self._live(k) else 0
    def incr(self, k): self.kv[k] = str(int(self.kv.get(k, 0)) + 1); return int(self.kv[k])
    def expire(self, k, s): self.exp[k] = time.time() + s
    def lpush(self, k, v): self.lists.setdefault(k, []).insert(0, v); return len(self.lists[k])
    def rpush(self, k, v): self.lists.setdefault(k, []).append(v)
    def llen(self, k): return len(self.lists.get(k, []))
    def lrange(self, k, a, b):
        l = self.lists.get(k, []); return [x.encode() for x in (l[a:] if b == -1 else l[a:b + 1])]
    def ltrim(self, k, a, b): self.lists[k] = self.lists.get(k, [])[a:b + 1]
    def lrem(self, k, n, v):
        l = self.lists.get(k, [])
        if v in l: l.remove(v); return 1
        return 0
    def blmove(self, src, dst, timeout, a, b):
        l = self.lists.get(src, [])
        if not l: return None
        v = l.pop(); self.lists.setdefault(dst, []).insert(0, v); return v.encode()
    def hincrby(self, k, f, n): h = self.hashes.setdefault(k, {}); h[f] = str(int(h.get(f, 0)) + n)
    def hincrbyfloat(self, k, f, n): h = self.hashes.setdefault(k, {}); h[f] = str(float(h.get(f, 0)) + n)
    def hgetall(self, k): return {a.encode(): b.encode() for a, b in self.hashes.get(k, {}).items()}
    def scan_iter(self, pat):
        pre = pat.rstrip("*")
        return iter([k for k in list(self.kv) if k.startswith(pre) and self._live(k)])
