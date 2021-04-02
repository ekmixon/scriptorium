#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# scriptorium
# https://github.com/honestpuck/scriptorium
# software system for handling the scripts in Jamf Pro
# providing easy editing and a revision system

""" software system for handling scripts in Jamf Pro """
__program__ = "scriptorium"
__author__ = "Tony Williams"
__email__ = "tonyw@honestpuck.com"
__copyright__ = "Copyright (c) 2021 Tony Williams"
__license__ = "MIT"
__version__ = "0.8"

import os
from sys import argv, stderr
import argparse
import plistlib
import xml.etree.ElementTree as ET
import logging.handlers
import requests
import subprocess

LOGLEVEL = logging.DEBUG

# where we stash the XML files
xml_dir = "~/work/test/xml"

# where we stash the script files
sh_dir = "~/work/test/scripts"

# prefs file
prefs_file = "~/Library/Preferences/com.github.autopkg.stage.plist"

LOGFILE = f"/usr/local/var/log/{__program__}.log"
logger = logging.getLogger(__program__)


def info(msg):
    print(msg)
    logger.info(msg)


class Jamf:
    """ Exists to carry some variables"""

    def __init__(self):
        self.scriptsURL = ""  # URL for accessing JPC scripts
        self.auth = ""  # name and password for JPC
        self.hdrs = ""  # header for requests, for JSON instead of XML
        # cookie for requests, makes sure you hit the same server each time
        # not required (yet)
        # self.cookies = ""
        # for this software
        self.xml_dir = ""
        self.sh_dir = ""


class Parser:
    """ Parses the command line and runs the right function """

    def __init__(self):
        """ build our command line parser """
        parser = argparse.ArgumentParser(
            epilog="for command help: `scriptorium <command> -h`"
        )
        subparsers = parser.add_subparsers(description="", required=True)

        #
        # create parser for `list`
        #
        parser_ls = subparsers.add_parser(
            "list", help="lists all scripts on the server"
        )
        parser_ls.set_defaults(func=Scripts.do_list)

        #
        # create parser for `down`
        #
        parser_down = subparsers.add_parser(
            "down", help="downloads all scripts out of the server"
        )
        parser_down.add_argument(
            "-n",
            "--no-force",
            help="don't force overwrite of existing script or XML file",
            action="store_true",
        )
        group = parser_down.add_mutually_exclusive_group()
        group.add_argument(
            "-p",
            "--push",
            help="do a git push after commit",
            action="store_true",
        )
        group.add_argument(
            "-n",
            "--no-commit",
            help="don't do a commit",
            action="store_true",
        )
        parser_down.set_defaults(func=Scripts.do_down)

        #
        # create parser for 'up'
        #
        parser_up = subparsers.add_parser(
            "up", help="add new or changed scripts and commit"
        )
        group = parser_up.add_mutually_exclusive_group()
        group.add_argument(
            "-p",
            "--push",
            help="do a git push after commit",
            action="store_true",
        )
        group.add_argument(
            "-n",
            "--no-commit",
            help="don't do a commit",
            action="store_true",
        )
        parser_up.add_argument(
            "-m",
            "--message",
            help="set commit message",
        )
        parser_up.set_defaults(func=Scripts.do_up)

        # create parser for `rename`
        parser_re = subparsers.add_parser("rename", help="rename a script")
        group = parser_re.add_mutually_exclusive_group()
        group.add_argument(
            "-p",
            "--push",
            help="do a git push after commit",
            action="store_true",
        )
        group.add_argument(
            "-n",
            "--no-commit",
            help="don't do a commit",
            action="store_true",
        )
        parser_up.add_argument(
            "-m",
            "--message",
            help="set commit message",
        )
        parser_re.add_argument("src", help="current name of script")
        parser_re.add_argument("dst", help="new name of script")
        parser_re.set_defaults(func=Scripts.do_rename)

        # create parser for `rm`
        parser_rm = subparsers.add_parser(
            "remove", help="remove (or delete) script from system"
        )
        parser_rm.add_argument("name", help="name of script to remove")
        group = parser_rm.add_mutually_exclusive_group()
        group.add_argument(
            "-p",
            "--push",
            help="do a git push after commit",
            action="store_true",
        )
        group.add_argument(
            "-n",
            "--no-commit",
            help="don't do a commit",
            action="store_true",
        )
        parser_up.add_argument(
            "-m",
            "--message",
            help="set commit message",
        )
        parser_rm.set_defaults(func=Scripts.do_rm)

        # create parser for `add`
        parser_add = subparsers.add_parser("add", help="add script to system")
        parser_add.add_argument("-f", "--filename", help="name of new script")
        parser_add.add_argument("-c", "--category", help="category of script")
        parser_add.add_argument("-n", "--notes", help="note about script")
        group = parser_add.add_mutually_exclusive_group()
        group.add_argument(
            "-a",
            "--after",
            help="run script with priority 'after'",
            action="store_true",
        )
        group.add_argument(
            "-b",
            "--before",
            help="run script with priority 'before'",
            action="store_true",
        )
        group.add_argument(
            "-r", "--reboot", help="run script at reboot", action="store_true"
        )
        parser_add.add_argument(
            "-z", "--zero", help="zero parameters for script"
        )
        group = parser_add.add_mutually_exclusive_group()
        group.add_argument(
            "-p",
            "--push",
            help="do a git push after commit",
            action="store_true",
        )
        group.add_argument(
            "-n",
            "--no-commit",
            help="don't do a commit",
            action="store_true",
        )
 parser_add.set_defaults(func=Scripts.do_add)
        self.parser = parser


class ScriptError(Exception):
    def __init__(self, message):
        print(f"scriptorium: error: {message}", file=stderr)
        logger.error(f"scriptorium: error: {message}")
        exit(1)


class Scripts:
    """ doing all the work """

    def setup_logging():
        """Defines a nicely formatted logger"""

        ch = logging.handlers.TimedRotatingFileHandler(
            LOGFILE, when="D", interval=1, backupCount=7
        )
        ch.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(ch)
        logger.setLevel(LOGLEVEL)

    def commit(args, jpc):
        """ do a git commit """
        """ this handles no commit or commit, and """
        """ optionally, a push on both directories """
        if args.no_commit:
            return
        msg = args.message if args.message else ' '.join(args[1:])
        command = ["git", "commit", "-a", "-m", message]
        # no-verify means no pre-commit hook
        if no_verify:
            command.append("-no-verify")
        complete = subprocess.run(command, text=True, capture_output=True)
        if complete.returncode != 0:
            # git can print a heap so give our user just the first 5 lines
            lines = complete.stderr.split("\n")
            for i in lines[0:5]:
                print(i)
            raise ScriptError("git commit in scripts directory failed")
        info(complete.stdout)
        if args.push:
            command = ["git", "push"]
            logger.info("pushing")
            complete = subprocess.run(command, text=True, capture_output=True)
            if complete.returncode != 0:
                # git can print a heap so give our user
                # just the first 5 lines
                lines = complete.stderr.split("\n")
                for i in lines[0:5]:
                    print(i)
                raise ScriptError("git push in scripts directory failed")
            info("Scripts Directory:")
            print(complete.stdout)
            logger.debug(complete.stdout)
            os.chdir(jpc.xml_dir)
            complete = subprocess.run(command, text=True, capture_output=True)
            if complete.returncode != 0:
                # git can print a heap so give our user
                # just the first 5 lines
                lines = complete.stderr.split("\n")
                for i in lines[0:5]:
                    print(i)
                raise ScriptError("git push in XML directory failed")
            info("XML Directory:")
            print(complete.stdout)
            logger.debug(complete.stdout)

    def do_list(args, jpc):
        """ subcommand `list` """
        logger.info("list command")
        # JSON is easier to deal with so use the header
        ret = requests.get(jpc.scriptsURL, auth=jpc.auth, headers=jpc.hdrs)
        if ret.status_code != 200:
            raise ScriptError(f"list get failed with error: {ret.status_code}")
        for script in ret.json()["scripts"]:
            idn = script["id"]
            name = script["name"]
            print(f"{idn}:\t{name}")
        logger.info("list succeeded")
        exit()

    def do_down(args, jpc):
        """ subcommand `down` """
        logger.info(' '.join(args[1:]))
        ret = requests.get(jpc.scriptsURL, auth=jpc.auth, headers=jpc.hdrs)
        if ret.status_code != 200:
            raise ScriptError(f"list get failed with error: {ret.status_code}")
        for script in ret.json()["scripts"]:
            idn = script["id"]
            name = script["name"]
            # we want XML so don't use the header
            ret = requests.get(f"{jpc.scriptsURL}/id/{idn}", auth=jpc.auth)
            if ret.status_code != 200:
                raise ScriptError(
                    f"script get failed: {ret.status_code} : {ret.url}"
                )
            xml = ret.text
            root = ET.fromstring(xml)
            text = root.findtext("script_contents")
            xml_filepath = f"{jpc.xml_dir}/{name}"
            sh_filepath = f"{jpc.sh_dir}/{name}"
            if args.force or not os.path.isfile(xml_filepath):
                info(f"Writing XML {name}")
                with open(xml_filepath, "w") as fp:
                    fp.write(xml)
            if args.force or not os.path.isfile(sh_filepath):
                info(f"Writing script {name}")
                with open(sh_filepath, "w") as fp:
                    fp.write(text)
        commit(args, jpc)
        exit()

    def do_up(args, jpc):
        logger.info(' '.join(args[1:]))
        # first change to scripts directory
        os.chdir(jpc.sh_out)
        # then see if we do have scripts to be done
        command = ["git", "diff", "--name-only", "-z", "HEAD"]
        complete = subprocess.run(command, text=True, capture_output=True)
        if complete.returncode != 0:
            # git diff prints a heap so give our user just the first 5 lines
            lines = complete.stderr.split("\n")
            for i in lines[0:5]:
                print(i)
            raise ScriptError("git diff in scripts directory failed")
        if complete.stdout == "":
            raise ScriptError("No files to process")
            exit(1)
        # we have work to do
        files = complete.stdout.split("\0")
        lst = " ".join(files[:-1])
        if not args.no_commit:
            info(f"Processing {lst}")
            msg = args.message if args.message else f"Up: {lst}"
            Scripts.commit(args, jpc, msg)
        for fn in lst:
            print(f"Processing {fn}")
            logger.debug(f"Processing {fn}")
            # first get our script
            with open(f"{jpc.sh_out}/{fn}", "r") as fp:
                scrpt = fp.read()
            x_file = jpc.xml_out + "/" + fn
            xml = ET.parse(x_file)
            root = xml.getroot()
            root.find("script_contents").text = scrpt
            # blank the encoded field as you can't have both in an upload
            root.find("script_contents_encoded").text = ""
            idn = root.findtext("id")
            data = ET.tostring(root)
            url = f"{jpc.scriptsURL}/id/{idn}"
            ret = requests.put(url, auth=jpc.auth, data=data)
            if ret.status_code != 201:
                print(f"failed to write to JPC: {ret.status_code}: {url}")
                logger.debug(
                    f"failed to write to JPC: {ret.status_code}: {url}"
                )
                exit(1)
            xml.write(x_file)
        commit(args, jpc, msg)
        exit()

    def do_rename(args, jpc):
        logger.info(' '.join(args[1:]))
        # go to XML dir
        os.chdir(jpc.xml_dir)
        xml = ET.parse(args.src)
        root = xml.getroot()
        idn = root.findtext('id')
        root.find('name') = args.dst
        root.find('script_contents_encoded') = ''
        data = ET.tostring(root)
        url = f"{jpc.scriptsURL}/id/{idn}"
        ret = requests.put(url, auth=jpc.auth, data=data)
        if ret.status_code != 201:
            raise ScriptError(f"failed to write to JPC: {ret.status_code}: {url}")
        command = ['git', 'mv', src, dst]
        logger.info("git mv in XML directory")
        complete = subprocess.run(command, text=True, capture_output=True)
        if complete.returncode != 0:
            # git can print a heap so give our user
            # just the first 5 lines
            lines = complete.stderr.split("\n")
            for i in lines[0:5]:
                print(i)
            raise ScriptError("git mv in XML directory failed")
        if not args.no_commit
            commit(args, jpc, ' '.join(command))
        os.chdir(jpc.xml_dir)
        logger.info("git mv in script directory")
        complete = subprocess.run(command, text=True, capture_output=True)
        if complete.returncode != 0:
            # git can print a heap so give our user
            # just the first 5 lines
            lines = complete.stderr.split("\n")
            for i in lines[0:5]:
                print(i)
            raise ScriptError("git mv in scripts directory failed")
        if not args.no_commit
            commit(args, jpc)
        exit()

    def do_rm(args, jpc):
        print("rm not implemented")
        exit()

    def do_add(args, jpc):
        print("add not implemented")
        if args.filename:
            print(args.filename)
        exit()

    def main():
        Scripts.setup_logging()
        logger.info("Start")
        jpc = Jamf()
        # sanity check
        jpc.xml_dir = os.path.expanduser(xml_dir)
        jpc.sh_dir = os.path.expanduser(sh_dir)
        if not os.path.isdir(jpc.xml_dir):
            print(f"directory {jpc.xml_dir} does not exist")
            exit(1)
        if not os.path.isdir(jpc.sh_dir):
            print(f"directory {jpc.sh_dir} does not exist")
            exit(1)
        # get prefs
        plist = os.path.expanduser(prefs_file)
        fp = open(plist, "rb")
        prefs = plistlib.load(fp)
        # because we only operate on scripts create a URL for the endpoint
        jpc.scriptsURL = f"{prefs['JSS_URL']}/JSSResource/scripts"
        # for the whole list JSON is handier so
        jpc.hdrs = {"Accept": "application/json"}
        jpc.auth = (prefs["API_USERNAME"], prefs["API_PASSWORD"])

        logger.debug("Prefs loaded")

        fred = Parser()
        # handle no arguments on command line
        if len(argv) == 1:
            print("Missing subcommand")
            fred.parser.print_help()
            exit(1)
        args = fred.parser.parse_args()
        # we never return from below call
        if args:
            args.func(args, jpc)
        else:
            exit()


if __name__ == "__main__":
    Scripts.main()
