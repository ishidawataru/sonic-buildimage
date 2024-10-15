import grpc
import sys
import logging
import asyncio

import emulator_pb2 as pb2
import emulator_pb2_grpc

from cli import Context
from cli import Command as BaseCommand
from cli import *

from eeprom import XcvrEEPROM as EEPROM

import argparse

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit import patch_stdout

stdout = logging.getLogger("stdout")
stderr = logging.getLogger("stderr")

from sonic_platform_base.sonic_xcvr.codes.public import cmis as cmis_codes
from sonic_platform_base.sonic_xcvr.mem_maps.public.cmis import CmisMemMap
from sonic_platform_base.sonic_xcvr.fields import consts


class TransceiverCommand(BaseCommand):
    @property
    def conn(self):
        return self.context.root().conn

    @property
    def eeprom(self):
        return self.context.eeprom

    @property
    def read(self):
        return self.context.read

    @property
    def write(self):
        return self.context.write

    @property
    def mem_map(self):
        return self.context.mem_map


def atoi(l, default=0):
    if len(l) == 0:
        return default
    return int(l[0], 0)


def print_field(field, prefix):
    for k, v in field.items():
        if type(v) is dict:
            print(f"{prefix}{k}:")
            print_field(v, prefix + "  ")
        else:
            print(f"{prefix}{k}: {v}")


class Read(TransceiverCommand):
    def __init__(self, context, parent, name, **options):
        super().__init__(context, parent, name, **options)
        self._arguments = []
        self.renamed = {}
        for key in self.mem_map._get_all_fields().keys():
            if " " in key:
                new = key.replace(" ", "-")
                self.renamed[new] = key
                self._arguments.append(new)
            else:
                self._arguments.append(key)

    def arguments(self):
        return self._arguments

    def exec(self, line):
        if len(line) == 0:
            print("No field specified")
            return
        name = line[0]
        try:
            (page, offset) = name.split(":")
            page = int(page, 0)
            offset = int(offset, 0)
            v = self.read(pb2.ReadRequest(page=page, offset=offset, length=1))
            print(f"{name}: {v.data[0]:0b}")
            return
        except ValueError:
            pass

        if name in self.renamed:
            name = self.renamed[name]

        try:
            field = self.mem_map.get_field(name)
        except AttributeError:
            print(f"Unknown field: {name}")
            return
        field = self.eeprom.read(name)
        print_field({name: field}, "")


class ReadRaw(TransceiverCommand):
    def exec(self, line):
        if len(line) == 0:
            print("No field specified")
            return
        name = line[0]
        (page, offset) = name.split(":")
        page = int(page, 0)
        offset = int(offset, 0)
        v = self.read(pb2.ReadRequest(page=page, offset=offset, length=1))
        print(f"{name}: {v.data[0]:0b}")


class Write(TransceiverCommand):
    def __init__(self, context, parent, name, **options):
        super().__init__(context, parent, name, **options)
        self._arguments = []
        self.renamed = {}
        for key in self.mem_map._get_all_fields().keys():
            if " " in key:
                new = key.replace(" ", "-")
                self.renamed[new] = key
                self._arguments.append(new)
            else:
                self._arguments.append(key)

    def arguments(self):
        return self._arguments

    def exec(self, line):
        if len(line) < 2:
            print("Not enough arguments")
            return

        name = line[0]
        if name in self.renamed:
            name = self.renamed[name]

        try:
            field = self.mem_map.get_field(name)
        except AttributeError:
            print(f"Unknown field: {name}")
            return
        self.eeprom.write(name, int(line[1], 0))
        return


class Remove(TransceiverCommand):
    def exec(self, line):
        try:
            req = pb2.UpdateInfoRequest(index=self.context.index, present=False)
        except ValueError as e:
            print(e)
            return
        self.conn.UpdateInfo(req)


class Insert(TransceiverCommand):
    def exec(self, line):
        try:
            req = pb2.UpdateInfoRequest(index=self.context.index, present=True)
        except ValueError as e:
            print(e)
            return
        self.conn.UpdateInfo(req)


class Info(TransceiverCommand):
    def exec(self, line):
        try:
            res = self.conn.GetInfo(pb2.GetInfoRequest(index=self.context.index))
        except ValueError as e:
            print(e)
            return
        print(res)


class TransceiverContext(Context):
    def __init__(self, parent, index):
        super().__init__(parent, fuzzy_completion=True)
        self.index = index

        codes = cmis_codes.CmisCodes
        mem_map = CmisMemMap(codes)

        self.mem_map = mem_map

        self.eeprom = EEPROM(index, parent.conn, mem_map)

        self.read = self.parent.conn.Read
        self.write = self.parent.conn.Write

        self.add_command("read", Read)
        self.add_command("read-raw", ReadRaw)
        self.add_command("write", Write)
        self.add_command("remove", Remove)
        self.add_command("insert", Insert)
        self.add_command("info", Info)

    def __str__(self):
        return f"transceiver({self.index})"


class Transceiver(Command):
    def __init__(self, context, parent, name, **options):
        super().__init__(context, parent, name, **options, no_completion_on_exec=True)

    def arguments(self):
        res = self.context.conn.List(pb2.ListRequest())
        return [str(info.index) for info in res.infos]

    def exec(self, line):
        if len(line) != 1:
            print("usage: transceiver <index>")
            return
        try:
            index = atoi(line)
        except ValueError:
            print("Invalid index")
            return
        return TransceiverContext(self.context, index)


class List(Command):
    def exec(self, line):
        res = self.context.conn.List(pb2.ListRequest())
        for info in res.infos:
            print(f"{info.index}: present: {info.present}")


class Root(Context):

    def __init__(self, conn):
        super().__init__(None, fuzzy_completion=True)
        self.conn = conn

        self.add_command("list", List)
        self.add_command("transceiver", Transceiver)

    def __str__(self):
        return ""


class Shell:
    def __init__(self, conn, default_prompt="> "):
        self.context = Root(conn)
        self.default_input = ""
        self.default_prompt = default_prompt

    def prompt(self):
        c = self.context
        l = [str(c)]
        while c.parent:
            l.append(str(c.parent))
            c = c.parent
        return ("/".join(reversed(l))[1:] if len(l) > 1 else "") + self.default_prompt

    def completer(self):
        return self.context.completer

    def exec(self, cmd: list):
        ret = self.context.exec(cmd)
        if ret:
            self.context = ret
        self.default_input = ""

    def bindings(self):
        b = KeyBindings()

        @b.add("?")
        def _(event):
            buf = event.current_buffer
            original_text = buf.text
            help_msg = event.app.shell.context.help(buf.text)
            buf.insert_text("?")
            buf.insert_line_below(copy_margin=False)
            buf.insert_text(help_msg)
            event.app.exit("")
            event.app.shell.default_input = original_text

        return b


async def loop_async(shell):
    session = PromptSession()

    with patch_stdout.patch_stdout():
        while True:
            c = shell.completer()
            p = shell.prompt()
            b = shell.bindings()
            session.app.shell = shell
            try:
                line = await session.prompt_async(
                    p, completer=c, key_bindings=b, default=shell.default_input
                )
            except KeyboardInterrupt:
                stderr.info("Execute 'exit' to exit")
                continue

            if len(line) > 0:
                shell.exec(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("-c", "--command-string")
    parser.add_argument("-k", "--keep-open", action="store_true")
    parser.add_argument("-x", "--stdin", action="store_true")
    args = parser.parse_args()

    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)-5s][%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger = logging.getLogger("xcvr-emu")
    logger.addHandler(console)
    console.setLevel(logging.DEBUG)  # emit all messages sent to this handler
    v = args.verbose
    if v == 0:
        logger.setLevel(logging.ERROR)
    elif v == 1:
        logger.setLevel(logging.INFO)
    else:  # v > 1
        logger.setLevel(logging.DEBUG)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG)
    shf = logging.Formatter("%(message)s")
    sh.setFormatter(shf)

    stdout.setLevel(logging.DEBUG)
    stdout.addHandler(sh)

    sh2 = logging.StreamHandler(sys.stderr)
    sh2.setLevel(logging.DEBUG)
    sh2.setFormatter(shf)

    stderr.setLevel(logging.DEBUG)
    stderr.addHandler(sh2)

    channel = grpc.insecure_channel("localhost:50051")
    conn = emulator_pb2_grpc.SfpEmulatorServiceStub(channel)

    shell = Shell(conn)

    async def _main():

        if args.stdin or args.command_string:
            stream = sys.stdin if args.stdin else args.command_string.split(";")
            for line in stream:
                await shell.exec(line, no_fail=False)
            if not args.keep_open:
                return

        tasks = [loop_async(shell)]

        await asyncio.gather(*tasks)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
