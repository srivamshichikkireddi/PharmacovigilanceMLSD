"""D5: structured JSON logs. A correlation id (== run id) is attached to every line by a
contextvar, so `docker compose logs | grep <run_id>` shows the whole run across services."""
import contextvars, json, logging, sys, time

correlation_id = contextvars.ContextVar("correlation_id", default="-")
current_step = contextvars.ContextVar("current_step", default="-")


class JsonFormatter(logging.Formatter):
    def __init__(self, service):
        super().__init__()
        self.service = service

    def format(self, r):
        d = {"ts": round(time.time(), 3), "level": r.levelname, "service": self.service,
             "correlation_id": correlation_id.get(), "step": current_step.get(), "msg": r.getMessage()}
        extra = getattr(r, "kv", None)
        if extra:
            d.update(extra)
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, default=str)


def setup_logging(service):
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)
    return logging.getLogger(service)


def log_kv(logger, msg, **kv):
    logger.info(msg, extra={"kv": kv})
