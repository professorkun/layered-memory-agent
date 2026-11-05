"""允许 ``python -m layered_memory <命令>``。"""
from .cli import main
import sys

sys.exit(main())
