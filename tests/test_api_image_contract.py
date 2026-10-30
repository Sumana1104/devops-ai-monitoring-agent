"""Source-level regression guards; actual image size/build still needs Docker CI."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class APIImageContractTests(unittest.TestCase):
    def test_default_requirements_delegate_to_small_api_runtime(self):
        lines = [line.strip() for line in (ROOT/'ai_service/requirements.txt').read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        self.assertEqual(lines, ['-r requirements-api.txt'])

    def test_api_runtime_does_not_declare_local_embeddings_or_cuda(self):
        names = {re.split(r'[<>=!~\[]', line.strip())[0].replace('_','-').lower()
                 for line in (ROOT/'ai_service/requirements-api.txt').read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')}
        self.assertTrue({'fastapi','uvicorn','requests','pydantic','opentelemetry-sdk'} <= names)
        self.assertTrue(names.isdisjoint({'chromadb','sentence-transformers','langgraph','torch','triton'}))
        self.assertFalse(any(name.startswith(('nvidia-', 'cuda-')) for name in names))

    def test_served_api_entrypoint_does_not_import_local_agent_dependencies(self):
        tree = ast.parse((ROOT/'ai_service/app/main.py').read_text())
        imports = {node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node,ast.ImportFrom) and node.module}
        imports.update(alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node,ast.Import) for alias in node.names)
        self.assertTrue(imports <= {'logging','os','sys','requests','fastapi','pydantic','opentelemetry'})

    def test_docker_uses_api_list_and_ci_caps_size_before_saving(self):
        dockerfile = (ROOT/'ai_service/Dockerfile').read_text()
        self.assertIn('COPY requirements-api.txt /tmp/requirements-api.txt',dockerfile)
        self.assertIn('-r /tmp/requirements-api.txt',dockerfile)
        ci = (ROOT/'.github/workflows/ci.yml').read_text()
        self.assertIn('1073741824',ci)
        self.assertLess(ci.index('docker image inspect'),ci.index('docker save --output image.tar'))
        self.assertIn('needs: [tests, ai-flow]',ci)


if __name__ == '__main__':
    unittest.main()
