import sys
import importlib.machinery
import importlib.util
import types
import inspect

from bytecode.bytecode import Bytecode
from bytecode.instr import Instr


def formathack_hook__(value, format_spec=None):
    """
    Gets called whenever a value is formatted. Right now it's a silly implementation,
    but it can be expanded with all sorts of nasty hacks.
    """
    return f"{value} formatted with {format_spec}"


def formathack_rewrite_bytecode__(code):
    """
    Modifies a code object to override the behavior of the FORMAT_VALUE
    instructions used by f-strings.
    """
    decompiled = Bytecode.from_code(code)
    modified_instructions = []
    for instruction in decompiled:
        if instruction.name == 'FORMAT_VALUE':
            # 0x04 means that a format spec is present
            if instruction.arg & 0x04 == 0x04:
                callback_arg_count = 2
            else:
                callback_arg_count = 1
            modified_instructions.extend([
                # Load in the callback
                Instr("LOAD_GLOBAL", "formathack_hook__"),
                # Shuffle around the top of the stack to put the arguments on top
                # of the CALL_FUNCTION instruction
                Instr("ROT_THREE" if callback_arg_count == 2 else "ROT_TWO"),
                # Call the callback function instead of executing FORMAT_VALUE
                Instr("CALL_FUNCTION", callback_arg_count)
            ])
        # Kind of nasty: we want to recursively alter the code of functions.
        elif instruction.name == 'LOAD_CONST' and isinstance(instruction.arg, types.CodeType):
            modified_instructions.extend([
                Instr("LOAD_CONST", formathack_rewrite_bytecode__(instruction.arg), lineno=instruction.lineno)
            ])
        else:
            modified_instructions.append(instruction)
    modified_bytecode = Bytecode(modified_instructions)
    # For functions, copy over argument definitions
    modified_bytecode.argnames = decompiled.argnames
    modified_bytecode.argcount = decompiled.argcount
    modified_bytecode.name = decompiled.name
    return modified_bytecode.to_code()


class _FormatHackLoader(importlib.machinery.SourceFileLoader):
    """
    A module loader that modifies the code of the modules it imports to override
    the behavior of f-strings. Nasty stuff.
    """
    @classmethod
    def find_spec(cls, name, path, target=None):
        # Start out with a spec from a default finder
        spec = importlib.machinery.PathFinder.find_spec(
            fullname=name,
             # Only apply to modules and packages in the current directory
             # This prevents standard library modules or site-packages
             # from being patched.
            path=[""],
            target=target
        )
        if spec is None:
            return None

        # Modify the loader in the spec to this loader
        spec.loader = cls(name, spec.origin)
        return spec

    def get_code(self, fullname):
        # This is called by exec_module to get the code of the module
        # to execute it.
        code = super().get_code(fullname)
        # Rewrite the code to modify the f-string formatting opcodes
        rewritten_code = formathack_rewrite_bytecode__(code)
        return rewritten_code

    def exec_module(self, module):
        # We introduce the callback that hooks into the f-string formatting
        # process in every imported module
        module.__dict__["formathack_hook__"] = formathack_hook__
        return super().exec_module(module)


def install():
    # If the _FormatHackLoader is not registered as a finder, do it now!
    if sys.meta_path[0] is not _FormatHackLoader:
        sys.meta_path.insert(0, _FormatHackLoader)
        # Tricky part: we want to be able to use our custom f-string behavior
        # in the main module where install was called. That module was loaded
        # with a standard loader though, so that's impossible without additional
        # dirty hacks.
        # Here, we execute the module _again_, this time with _FormatHackLoader
        module_globals = inspect.currentframe().f_back.f_globals
        module_name = module_globals["__name__"]
        module_file = module_globals["__file__"]
        loader = _FormatHackLoader(module_name, module_file)
        loader.load_module(module_name)
        # This is actually pretty important. If we don't exit here, the main module
        # will continue from the formathack.install method, causing it to run twice!
        sys.exit(0)
