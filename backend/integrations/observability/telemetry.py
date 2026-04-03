

class DummyTracer:
    def start_as_current_span(self, name, *args, **kwargs):
        class DummySpan:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def set_attribute(self, *args):
                pass

        return DummySpan()


class DummyMeter:
    def create_counter(self, *args, **kwargs):
        class DummyCounter:
            def add(self, *args, **kwargs):
                pass

        return DummyCounter()


def get_tracer(name):
    return DummyTracer()


def get_meter(name):
    return DummyMeter()
