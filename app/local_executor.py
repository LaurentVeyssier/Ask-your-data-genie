# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Custom local code executor that handles input and output files on the filesystem."""

import base64
import io
import logging
import mimetypes
import multiprocessing
import queue
import re
import os
import traceback
from contextlib import redirect_stdout
from typing import Any, List, Optional

from google.adk.agents.invocation_context import InvocationContext
from google.adk.code_executors.base_code_executor import BaseCodeExecutor
from google.adk.code_executors.code_execution_utils import (
    CodeExecutionInput,
    CodeExecutionResult,
    File,
)
from typing_extensions import override

# Initialize logger
logger: logging.Logger = logging.getLogger("google_adk." + __name__)


def _execute_in_process(
    code: str, globals_: dict[str, Any], result_queue: multiprocessing.Queue
) -> None:
    """Executes code in a separate process and puts result in queue.

    Args:
        code: The python code to execute.
        globals_: The global context dictionary.
        result_queue: Multiprocessing Queue to send the stdout and errors back.
    """
    stdout = io.StringIO()
    error = None
    try:
        with redirect_stdout(stdout):
            exec(code, globals_, globals_)
    except BaseException:
        error = traceback.format_exc()
    result_queue.put((stdout.getvalue(), error))


def _prepare_globals(code: str, globals_: dict[str, Any]) -> None:
    """Prepare globals for code execution, injecting __name__ if needed.

    Args:
        code: The python code block.
        globals_: The global context dictionary to update.
    """
    if re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]", code):
        globals_["__name__"] = "__main__"


class FileSavingLocalCodeExecutor(BaseCodeExecutor):
    """A local code executor that saves input files before running the code

    and automatically captures generated files as output artifacts.
    """

    # Enable optimize_data_file so that ADK processes inline CSV files correctly
    optimize_data_file: bool = True

    # Override delimiters to avoid tool_code/tool_output strings that trigger
    # backend validation issues/errors.
    code_block_delimiters: List[tuple[str, str]] = [
        ("```python\n", "\n```"),
    ]
    execution_result_delimiters: tuple[str, str] = (
        "```\nCode execution result:\n",
        "\n```",
    )

    @override
    def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Executes code locally in a subprocess, saving input files and capturing output files.

        Args:
            invocation_context: The context of the current agent invocation.
            code_execution_input: The input structure containing the code and files.

        Returns:
            A CodeExecutionResult containing the stdout, stderr, and output files.
        """
        written_files: List[str] = []

        # 1. Write input files to the filesystem (current working directory)
        for file in code_execution_input.input_files:
            try:
                # The file content is base64-encoded string
                file_data: bytes = base64.b64decode(file.content.encode("utf-8"))
                with open(file.name, "wb") as f:
                    f.write(file_data)
                written_files.append(file.name)
                logger.info("Wrote input file: %s", file.name)
            except Exception as e:
                logger.error("Failed to write input file %s: %s", file.name, e)

        # 2. Snapshot the current directory before execution to detect new files
        before_files = set(os.listdir("."))

        # 3. Execute the code in a spawned subprocess (isolated local context)
        globals_ = {}
        _prepare_globals(code_execution_input.code, globals_)

        ctx = multiprocessing.get_context("spawn")
        result_queue = ctx.Queue()
        process = ctx.Process(
            target=_execute_in_process,
            args=(code_execution_input.code, globals_, result_queue),
            daemon=True,
        )
        process.start()

        output = ""
        error = ""
        timeout = self.timeout_seconds or 30.0
        try:
            output, err = result_queue.get(timeout=timeout)
            process.join()
            if err:
                error = err
        except queue.Empty:
            process.terminate()
            process.join()
            error = f"Code execution timed out after {timeout} seconds."

        # Collect the final result
        result_queue.close()
        result_queue.join_thread()

        # 4. Snapshot the current directory after execution
        after_files = set(os.listdir("."))
        new_files = after_files - before_files

        # 5. Process newly created files and append to output_files
        output_files: List[File] = []
        for filepath in new_files:
            # Skip directories or hidden files
            if os.path.isdir(filepath) or filepath.startswith("."):
                continue

            # Skip files that we wrote ourselves as input files
            if filepath in written_files:
                continue

            try:
                # Read content and guess mimetype
                with open(filepath, "rb") as f:
                    file_bytes: bytes = f.read()

                # Base64 encode the content
                encoded_content: str = base64.b64encode(file_bytes).decode("utf-8")
                mime_type, _ = mimetypes.guess_type(filepath)
                if not mime_type:
                    mime_type = "application/octet-stream"

                output_files.append(
                    File(
                        name=filepath,
                        content=encoded_content,
                        mime_type=mime_type,
                    )
                )
                logger.info("Captured output file: %s (%s)", filepath, mime_type)
            except Exception as e:
                logger.error("Failed to process output file %s: %s", filepath, e)

        # 6. Cleanup input files and generated output files from disk
        for filepath in written_files:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception as e:
                    logger.warning("Failed to clean up input file %s: %s", filepath, e)

        for filepath in new_files:
            if os.path.exists(filepath) and not os.path.isdir(filepath):
                try:
                    os.remove(filepath)
                except Exception as e:
                    logger.warning("Failed to clean up output file %s: %s", filepath, e)

        # Return results with captured output files
        return CodeExecutionResult(
            stdout=output,
            stderr=error,
            output_files=output_files,
        )
