from sa_home_node.plugins import Plugin, ServiceSpec
from sa_home_node.tasks.config import TasksConfig
from sa_home_node.tasks.run import make_handler, run_tasks

PLUGIN = Plugin(
    name="tasks",
    config_model=TasksConfig,
    services=(
        ServiceSpec(
            "tasks",
            run_tasks,
            handler=make_handler,
            process=True,
            endpoint=lambda config: config.socket,
        ),
    ),
)
