import logging
import threading

from watchful.registry import register

log = logging.getLogger("watchful.metrics")


@register("metrics", "log")
class LogMetrics:
    def __init__(self, name: str = "log", **_):
        pass

    def put(self, metric: str, value: float, dims: dict | None = None, unit: str = "None") -> None:
        log.info("metric %s=%s %s", metric, value, dims or {})

    def flush(self) -> None:
        pass


@register("metrics", "cloudwatch")
class CloudWatchMetrics:
    """Buffers points and sends them to CloudWatch in batches, namespace Watchful."""

    def __init__(self, region: str | None = None, settings=None, name: str = "cloudwatch", namespace: str = "Watchful",
                 **_):
        import boto3
        self.cw = boto3.client("cloudwatch", region_name=region or settings.aws_region)
        self.namespace = namespace
        self.buffer: list[dict] = []
        self.lock = threading.Lock()

    def put(self, metric: str, value: float, dims: dict | None = None, unit: str = "None") -> None:
        point = {"MetricName": metric, "Value": float(value), "Unit": unit,
                 "Dimensions": [{"Name": k, "Value": str(v)} for k, v in (dims or {}).items()]}
        with self.lock:
            self.buffer.append(point)
            full = len(self.buffer) >= 20
        if full:
            self.flush()

    def flush(self) -> None:
        with self.lock:
            points, self.buffer = self.buffer, []
        for i in range(0, len(points), 20):
            self.cw.put_metric_data(Namespace=self.namespace, MetricData=points[i:i + 20])
