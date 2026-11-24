from sa_home_node.services.registry import ServiceTable
from sa_home_node.tasks.config import TasksConfig
from sa_home_node.tasks.plugin import PLUGIN
from sa_home_node.tasks.run import make_handler, run_tasks


def test_plugin_spec():
    table = ServiceTable([PLUGIN])
    spec = table.spec("tasks")
    assert PLUGIN.name == "tasks"
    assert PLUGIN.config_model is TasksConfig
    assert (spec.run, spec.handler, spec.process) == (run_tasks, make_handler, True)
    assert spec.endpoint(TasksConfig(socket="/x.sock")) == "/x.sock"
    assert table.plugin_of("tasks") is PLUGIN
