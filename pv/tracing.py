"""D5: OpenTelemetry. Spans are exported to Jaeger over OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT
is set; otherwise `span()` is a no-op, so the code path is identical either way. The trace context
travels through the Redis queue inside the job ('traceparent'), so API and worker spans join up."""
import contextlib, os

_tracer = None
_prop = None


def init_tracing(service):
    global _tracer, _prop
    ep = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not ep:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        prov = TracerProvider(resource=Resource.create({"service.name": service}))
        prov.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=ep.rstrip("/") + "/v1/traces")))
        trace.set_tracer_provider(prov)
        _tracer, _prop = trace.get_tracer(service), TraceContextTextMapPropagator()
        return True
    except Exception as exc:  # noqa
        print(f"[tracing] disabled: {exc}")
        return False


def inject_context():
    if not _prop:
        return {}
    carrier = {}
    _prop.inject(carrier)
    return carrier


def extract_context(carrier):
    return _prop.extract(carrier or {}) if _prop else None


@contextlib.contextmanager
def span(name, parent=None, **attrs):
    if not _tracer:
        yield None
        return
    with _tracer.start_as_current_span(name, context=parent) as sp:
        for k, v in attrs.items():
            sp.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
        yield sp
