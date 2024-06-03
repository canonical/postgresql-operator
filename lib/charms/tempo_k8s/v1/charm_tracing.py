#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""This charm library contains utilities to instrument your Charm with opentelemetry tracing data collection.

(yes! charm code, not workload code!)

This means that, if your charm is related to, for example, COS' Tempo charm, you will be able to inspect
in real time from the Grafana dashboard the execution flow of your charm.

To start using this library, you need to do two things:
1) decorate your charm class with

`@trace_charm(tracing_endpoint="my_tracing_endpoint")`

2) add to your charm a "my_tracing_endpoint" (you can name this attribute whatever you like) **property**
that returns an otlp http/https endpoint url. If you are using the `TracingEndpointProvider` as
`self.tracing = TracingEndpointProvider(self)`, the implementation could be:

```
    @property
    def my_tracing_endpoint(self) -> Optional[str]:
        '''Tempo endpoint for charm tracing'''
        if self.tracing.is_ready():
            return self.tracing.otlp_http_endpoint()
        else:
            return None
```

At this point your charm will be automatically instrumented so that:
- charm execution starts a trace, containing
    - every event as a span (including custom events)
    - every charm method call (except dunders) as a span

if you wish to add more fine-grained information to the trace, you can do so by getting a hold of the tracer like so:
```
import opentelemetry
...
    @property
    def tracer(self) -> opentelemetry.trace.Tracer:
        return opentelemetry.trace.get_tracer(type(self).__name__)
```

By default, the tracer is named after the charm type. If you wish to override that, you can pass
a different `service_name` argument to `trace_charm`.

*Upgrading from `v0`:*

If you are upgrading from `charm_tracing` v0, you need to take the following steps (assuming you already
have the newest version of the library in your charm):
1) If you need the dependency for your tests, add the following dependency to your charm project
(or, if your project had a dependency on `opentelemetry-exporter-otlp-proto-grpc` only because
of `charm_tracing` v0, you can replace it with):

`opentelemetry-exporter-otlp-proto-http>=1.21.0`.

2) Update the charm method referenced to from `@trace` and `@trace_charm`,
to return from `TracingEndpointRequirer.otlp_http_endpoint()` instead of `grpc_http`. For example:

```
    from charms.tempo_k8s.v0.charm_tracing import trace_charm

    @trace_charm(
        tracing_endpoint="my_tracing_endpoint",
    )
    class MyCharm(CharmBase):

    ...

        @property
        def my_tracing_endpoint(self) -> Optional[str]:
            '''Tempo endpoint for charm tracing'''
            if self.tracing.is_ready():
                return self.tracing.otlp_grpc_endpoint()
            else:
                return None
```

needs to be replaced with:

```
    from charms.tempo_k8s.v1.charm_tracing import trace_charm

    @trace_charm(
        tracing_endpoint="my_tracing_endpoint",
    )
    class MyCharm(CharmBase):

    ...

        @property
        def my_tracing_endpoint(self) -> Optional[str]:
            '''Tempo endpoint for charm tracing'''
            if self.tracing.is_ready():
                return self.tracing.otlp_http_endpoint()
            else:
                return None
```

3) If you were passing a certificate using `server_cert`, you need to change it to provide an *absolute* path to
the certificate file.
"""

import functools
import inspect
import logging
import os
from contextlib import contextmanager
from contextvars import Context, ContextVar, copy_context
from pathlib import Path
from typing import (
    Any,
    Callable,
    Generator,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
    cast,
)

import opentelemetry
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import (
    INVALID_SPAN,
    Tracer,
    get_tracer,
    get_tracer_provider,
    set_span_in_context,
    set_tracer_provider,
)
from opentelemetry.trace import get_current_span as otlp_get_current_span
from ops.charm import CharmBase
from ops.framework import Framework

# The unique Charmhub library identifier, never change it
LIBID = "cb1705dcd1a14ca09b2e60187d1215c7"

# Increment this major API version when introducing breaking changes
LIBAPI = 1

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version

LIBPATCH = 6

PYDEPS = ["opentelemetry-exporter-otlp-proto-http==1.21.0"]

logger = logging.getLogger("tracing")

tracer: ContextVar[Tracer] = ContextVar("tracer")
_GetterType = Union[Callable[[CharmBase], Optional[str]], property]

CHARM_TRACING_ENABLED = "CHARM_TRACING_ENABLED"


def is_enabled() -> bool:
    """Whether charm tracing is enabled."""
    return os.getenv(CHARM_TRACING_ENABLED, "1") == "1"


@contextmanager
def charm_tracing_disabled():
    """Contextmanager to temporarily disable charm tracing.

    For usage in tests.
    """
    previous = os.getenv(CHARM_TRACING_ENABLED, "1")
    os.environ[CHARM_TRACING_ENABLED] = "0"
    yield
    os.environ[CHARM_TRACING_ENABLED] = previous


def get_current_span() -> Union[Span, None]:
    """Return the currently active Span, if there is one, else None.

    If you'd rather keep your logic unconditional, you can use opentelemetry.trace.get_current_span,
    which will return an object that behaves like a span but records no data.
    """
    span = otlp_get_current_span()
    if span is INVALID_SPAN:
        return None
    return cast(Span, span)


def _get_tracer_from_context(ctx: Context) -> Optional[ContextVar]:
    tracers = [v for v in ctx if v is not None and v.name == "tracer"]
    if tracers:
        return tracers[0]
    return None


def _get_tracer() -> Optional[Tracer]:
    """Find tracer in context variable and as a fallback locate it in the full context."""
    try:
        return tracer.get()
    except LookupError:
        try:
            ctx: Context = copy_context()
            if context_tracer := _get_tracer_from_context(ctx):
                return context_tracer.get()
            else:
                return None
        except LookupError:
            return None


@contextmanager
def _span(name: str) -> Generator[Optional[Span], Any, Any]:
    """Context to create a span if there is a tracer, otherwise do nothing."""
    if tracer := _get_tracer():
        with tracer.start_as_current_span(name) as span:
            yield cast(Span, span)
    else:
        yield None


_C = TypeVar("_C", bound=Type[CharmBase])
_T = TypeVar("_T", bound=type)
_F = TypeVar("_F", bound=Type[Callable])


class TracingError(RuntimeError):
    """Base class for errors raised by this module."""


class UntraceableObjectError(TracingError):
    """Raised when an object you're attempting to instrument cannot be autoinstrumented."""


def _get_tracing_endpoint(tracing_endpoint_getter, self, charm):
    if isinstance(tracing_endpoint_getter, property):
        tracing_endpoint = tracing_endpoint_getter.__get__(self)
    else:  # method or callable
        tracing_endpoint = tracing_endpoint_getter(self)

    if tracing_endpoint is None:
        logger.debug(
            f"{charm}.{tracing_endpoint_getter} returned None; quietly disabling "
            f"charm_tracing for the run."
        )
        return
    elif not isinstance(tracing_endpoint, str):
        raise TypeError(
            f"{charm}.{tracing_endpoint_getter} should return a tempo endpoint (string); "
            f"got {tracing_endpoint} instead."
        )
    else:
        logger.debug(f"Setting up span exporter to endpoint: {tracing_endpoint}/v1/traces")
    return f"{tracing_endpoint}/v1/traces"


def _get_server_cert(server_cert_getter, self, charm):
    if isinstance(server_cert_getter, property):
        server_cert = server_cert_getter.__get__(self)
    else:  # method or callable
        server_cert = server_cert_getter(self)

    if server_cert is None:
        logger.warning(
            f"{charm}.{server_cert_getter} returned None; sending traces over INSECURE connection."
        )
        return
    elif not Path(server_cert).is_absolute():
        raise ValueError(
            f"{charm}.{server_cert_getter} should return a valid tls cert absolute path (string | Path)); "
            f"got {server_cert} instead."
        )
    return server_cert


def _setup_root_span_initializer(
    charm: Type[CharmBase],
    tracing_endpoint_getter: _GetterType,
    server_cert_getter: Optional[_GetterType],
    service_name: Optional[str] = None,
):
    """Patch the charm's initializer."""
    original_init = charm.__init__

    @functools.wraps(original_init)
    def wrap_init(self: CharmBase, framework: Framework, *args, **kwargs):
        original_init(self, framework, *args, **kwargs)
        if not is_enabled():
            logger.info("Tracing DISABLED: skipping root span initialization")
            return

        # already init some attrs that will be reinited later by calling original_init:
        # self.framework = framework
        # self.handle = Handle(None, self.handle_kind, None)

        original_event_context = framework._event_context

        _service_name = service_name or self.app.name

        resource = Resource.create(
            attributes={
                "service.name": _service_name,
                "compose_service": _service_name,
                "charm_type": type(self).__name__,
                # juju topology
                "juju_unit": self.unit.name,
                "juju_application": self.app.name,
                "juju_model": self.model.name,
                "juju_model_uuid": self.model.uuid,
            }
        )
        provider = TracerProvider(resource=resource)
        try:
            tracing_endpoint = _get_tracing_endpoint(tracing_endpoint_getter, self, charm)
        except Exception:
            # if anything goes wrong with retrieving the endpoint, we go on with tracing disabled.
            # better than breaking the charm.
            logger.exception(
                f"exception retrieving the tracing "
                f"endpoint from {charm}.{tracing_endpoint_getter}; "
                f"proceeding with charm_tracing DISABLED. "
            )
            return

        if not tracing_endpoint:
            return

        server_cert: Optional[Union[str, Path]] = (
            _get_server_cert(server_cert_getter, self, charm) if server_cert_getter else None
        )

        exporter = OTLPSpanExporter(
            endpoint=tracing_endpoint,
            certificate_file=str(Path(server_cert).absolute()) if server_cert else None,
            timeout=2,
        )

        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
        set_tracer_provider(provider)
        _tracer = get_tracer(_service_name)  # type: ignore
        _tracer_token = tracer.set(_tracer)

        dispatch_path = os.getenv("JUJU_DISPATCH_PATH", "")

        # all these shenanigans are to work around the fact that the opentelemetry tracing API is built
        # on the assumption that spans will be used as contextmanagers.
        # Since we don't (as we need to close the span on framework.commit),
        # we need to manually set the root span as current.
        span = _tracer.start_span("charm exec", attributes={"juju.dispatch_path": dispatch_path})
        ctx = set_span_in_context(span)

        # log a trace id so we can look it up in tempo.
        root_trace_id = hex(span.get_span_context().trace_id)[2:]  # strip 0x prefix
        logger.debug(f"Starting root trace with id={root_trace_id!r}.")

        span_token = opentelemetry.context.attach(ctx)  # type: ignore

        @contextmanager
        def wrap_event_context(event_name: str):
            # when the framework enters an event context, we create a span.
            with _span("event: " + event_name) as event_context_span:
                if event_context_span:
                    # todo: figure out how to inject event attrs in here
                    event_context_span.add_event(event_name)
                yield original_event_context(event_name)

        framework._event_context = wrap_event_context  # type: ignore

        original_close = framework.close

        @functools.wraps(original_close)
        def wrap_close():
            span.end()
            opentelemetry.context.detach(span_token)  # type: ignore
            tracer.reset(_tracer_token)
            tp = cast(TracerProvider, get_tracer_provider())
            tp.force_flush(timeout_millis=1000)  # don't block for too long
            tp.shutdown()
            original_close()

        framework.close = wrap_close
        return

    charm.__init__ = wrap_init


def trace_charm(
    tracing_endpoint: str,
    server_cert: Optional[str] = None,
    service_name: Optional[str] = None,
    extra_types: Sequence[type] = (),
):
    """Autoinstrument the decorated charm with tracing telemetry.

    Use this function to get out-of-the-box traces for all events emitted on this charm and all
    method calls on instances of this class.

    Usage:
    >>> from charms.tempo_k8s.v1.charm_tracing import trace_charm
    >>> from charms.tempo_k8s.v1.tracing import TracingEndpointProvider
    >>> from ops import CharmBase
    >>>
    >>> @trace_charm(
    >>>         tracing_endpoint="tempo_otlp_http_endpoint",
    >>> )
    >>> class MyCharm(CharmBase):
    >>>
    >>>     def __init__(self, framework: Framework):
    >>>         ...
    >>>         self.tracing = TracingEndpointProvider(self)
    >>>
    >>>     @property
    >>>     def tempo_otlp_http_endpoint(self) -> Optional[str]:
    >>>         if self.tracing.is_ready():
    >>>             return self.tracing.otlp_http_endpoint()
    >>>         else:
    >>>             return None
    >>>
    :param server_cert: method or property on the charm type that returns an
        optional absolute path to a tls certificate to be used when sending traces to a remote server.
        If it returns None, an _insecure_ connection will be used.
    :param tracing_endpoint: name of a property on the charm type that returns an
        optional (fully resolvable) tempo url. If None, tracing will be effectively disabled. Else, traces will be
        pushed to that endpoint.
    :param service_name: service name tag to attach to all traces generated by this charm.
        Defaults to the juju application name this charm is deployed under.
    :param extra_types: pass any number of types that you also wish to autoinstrument.
        For example, charm libs, relation endpoint wrappers, workload abstractions, ...
    """

    def _decorator(charm_type: Type[CharmBase]):
        """Autoinstrument the wrapped charmbase type."""
        _autoinstrument(
            charm_type,
            tracing_endpoint_getter=getattr(charm_type, tracing_endpoint),
            server_cert_getter=getattr(charm_type, server_cert) if server_cert else None,
            service_name=service_name,
            extra_types=extra_types,
        )
        return charm_type

    return _decorator


def _autoinstrument(
    charm_type: Type[CharmBase],
    tracing_endpoint_getter: _GetterType,
    server_cert_getter: Optional[_GetterType] = None,
    service_name: Optional[str] = None,
    extra_types: Sequence[type] = (),
) -> Type[CharmBase]:
    """Set up tracing on this charm class.

    Use this function to get out-of-the-box traces for all events emitted on this charm and all
    method calls on instances of this class.

    Usage:

    >>> from charms.tempo_k8s.v1.charm_tracing import _autoinstrument
    >>> from ops.main import main
    >>> _autoinstrument(
    >>>         MyCharm,
    >>>         tracing_endpoint_getter=MyCharm.tempo_otlp_http_endpoint,
    >>>         service_name="MyCharm",
    >>>         extra_types=(Foo, Bar)
    >>> )
    >>> main(MyCharm)

    :param charm_type: the CharmBase subclass to autoinstrument.
    :param server_cert_getter: method or property on the charm type that returns an
        optional absolute path to a tls certificate to be used when sending traces to a remote server.
        This needs to be a valid path to a certificate.
    :param tracing_endpoint_getter: method or property on the charm type that returns an
        optional tempo url. If None, tracing will be effectively disabled. Else, traces will be
        pushed to that endpoint.
    :param service_name: service name tag to attach to all traces generated by this charm.
        Defaults to the juju application name this charm is deployed under.
    :param extra_types: pass any number of types that you also wish to autoinstrument.
        For example, charm libs, relation endpoint wrappers, workload abstractions, ...
    """
    logger.info(f"instrumenting {charm_type}")
    _setup_root_span_initializer(
        charm_type,
        tracing_endpoint_getter,
        server_cert_getter=server_cert_getter,
        service_name=service_name,
    )
    trace_type(charm_type)
    for type_ in extra_types:
        trace_type(type_)

    return charm_type


def trace_type(cls: _T) -> _T:
    """Set up tracing on this class.

    Use this decorator to get out-of-the-box traces for all method calls on instances of this class.
    It assumes that this class is only instantiated after a charm type decorated with `@trace_charm`
    has been instantiated.
    """
    logger.info(f"instrumenting {cls}")
    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        logger.info(f"discovered {method}")

        if method.__name__.startswith("__"):
            logger.info(f"skipping {method} (dunder)")
            continue

        isstatic = isinstance(inspect.getattr_static(cls, method.__name__), staticmethod)
        setattr(cls, name, trace_method(method, static=isstatic))

    return cls


def trace_method(method: _F, static: bool = False) -> _F:
    """Trace this method.

    A span will be opened when this method is called and closed when it returns.
    """
    return _trace_callable(method, "method", static=static)


def trace_function(function: _F) -> _F:
    """Trace this function.

    A span will be opened when this function is called and closed when it returns.
    """
    return _trace_callable(function, "function")


def _trace_callable(callable: _F, qualifier: str, static: bool = False) -> _F:
    logger.info(f"instrumenting {callable}")

    # sig = inspect.signature(callable)
    @functools.wraps(callable)
    def wrapped_function(*args, **kwargs):  # type: ignore
        name = getattr(callable, "__qualname__", getattr(callable, "__name__", str(callable)))
        with _span(f"{'(static) ' if static else ''}{qualifier} call: {name}"):  # type: ignore
            if static:
                # fixme: do we or don't we need [1:]?
                #  The _trace_callable decorator doesn't always play nice with @staticmethods.
                #  Sometimes it will receive 'self', sometimes it won't.
                # return callable(*args, **kwargs)  # type: ignore
                return callable(*args[1:], **kwargs)  # type: ignore
            return callable(*args, **kwargs)  # type: ignore

    # wrapped_function.__signature__ = sig
    return wrapped_function  # type: ignore


def trace(obj: Union[Type, Callable]):
    """Trace this object and send the resulting spans to Tempo.

    It will dispatch to ``trace_type`` if the decorated object is a class, otherwise
    ``trace_function``.
    """
    if isinstance(obj, type):
        if issubclass(obj, CharmBase):
            raise ValueError(
                "cannot use @trace on CharmBase subclasses: use @trace_charm instead "
                "(we need some arguments!)"
            )
        return trace_type(obj)
    else:
        try:
            return trace_function(obj)
        except Exception:
            raise UntraceableObjectError(
                f"cannot create span from {type(obj)}; instrument {obj} manually."
            )
