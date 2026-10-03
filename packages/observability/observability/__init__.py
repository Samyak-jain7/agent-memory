import json
import logging
from contextlib import contextmanager
from opentelemetry import trace, metrics
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from time import monotonic

logger = logging.getLogger("agent_memory")

@contextmanager
def operation(name, **ids):
    if __import__("os").environ.get("MEMORY_TELEMETRY")=="console":configure()
    # No payloads or exception messages enter telemetry.
    safe = {k: str(v) for k,v in ids.items() if k in {"tenant_id","actor_id","subject_id","request_id","episode_id","job_id","memory_id","provider","policy","outcome"}}
    started=monotonic()
    outcome="succeeded"
    meter=metrics.get_meter("agent_memory")
    with trace.get_tracer("agent_memory").start_as_current_span(name, attributes=safe,
            record_exception=False, set_status_on_exception=False) as span:
        try:
            yield span
        except Exception as error:
            outcome="failed"
            span.set_attribute("error.type", type(error).__name__)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            raise
        finally:
            dimensions={"operation":name,"outcome":outcome}
            meter.create_counter("memory.operation.calls").add(1,dimensions)
            meter.create_histogram("memory.operation.duration",unit="s").record(monotonic()-started,dimensions)
            logger.info(json.dumps({"operation":name, "trace_id":format(span.get_span_context().trace_id,"032x"), **safe}))

def carrier():
    result={};TraceContextTextMapPropagator().inject(result);return {k:v for k,v in result.items() if k=="traceparent"}


def extract_context(headers):
    # Drop baggage and tracestate: both permit caller-controlled plaintext values.
    return TraceContextTextMapPropagator().extract({'traceparent':headers.get('traceparent','')})


class SafeSpanExporter:
    """SDK exporter emitting ONLY our approved attributes, never event payloads."""
    def export(self,spans):
        from opentelemetry.sdk.trace.export import SpanExportResult
        for span in spans:
            if not __import__('re').fullmatch(r'memory\.[a-z_.]{1,64}',span.name):continue
            logger.info(json.dumps({'kind':'trace','operation':span.name,'trace_id':format(span.context.trace_id,'032x'),'span_id':format(span.context.span_id,'016x'),'parent_id':format(span.parent.span_id,'016x') if span.parent else None,'attributes':{k:v for k,v in (span.attributes or {}).items() if k in {'tenant_id','actor_id','subject_id','request_id','episode_id','job_id','memory_id','provider','policy','outcome','error.type','evaluation.version','model.cost_state','model.prompt_tokens','model.completion_tokens','model.total_tokens'}}}))
        return SpanExportResult.SUCCESS
    def shutdown(self):pass
    def force_flush(self,timeout_millis=30000):return True

_configured=False

def configure():
    """Local structured exporter. Attach a platform exporter only after privacy review."""
    global _configured
    if _configured:return
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader,ConsoleMetricExporter
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler());logger.setLevel(logging.INFO);logger.propagate=False
    provider=TracerProvider();provider.add_span_processor(SimpleSpanProcessor(SafeSpanExporter()))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(metric_readers=[PeriodicExportingMetricReader(ConsoleMetricExporter(),export_interval_millis=60000)]))
    _configured=True
