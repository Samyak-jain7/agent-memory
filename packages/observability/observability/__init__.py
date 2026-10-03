import json
import logging
from contextlib import contextmanager
from opentelemetry import trace, metrics
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from time import monotonic

logger = logging.getLogger("agent_memory")

@contextmanager
def operation(name, **ids):
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
