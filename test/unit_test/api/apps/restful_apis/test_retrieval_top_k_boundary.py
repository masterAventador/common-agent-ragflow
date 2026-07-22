#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

from pathlib import Path


def test_public_retrieval_handlers_share_the_bounded_top_k_validator():
    repository_root = Path(__file__).resolve().parents[5]
    handlers = {
        "chunk": (repository_root / "api/apps/restful_apis/chunk_api.py", 2),
        "dify": (repository_root / "api/apps/restful_apis/dify_retrieval_api.py", 2),
        "searchbot": (repository_root / "api/apps/restful_apis/bot_api.py", 3),
    }

    for name, (path, minimum_occurrences) in handlers.items():
        source = path.read_text(encoding="utf-8")
        assert source.count("validate_rest_api_top_k") >= minimum_occurrences, name
