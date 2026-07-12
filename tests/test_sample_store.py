import tempfile
import unittest
from pathlib import Path

from ghidra_lab_mcp.sample_store import SampleStore, StoreError


class SampleStoreTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_upload_rotates_bearer_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SampleStore(Path(tmp), "http://lab.test", 1024)
            pending = store.create_upload("sample.bin")
            sample_id = pending["sample_id"]
            upload_token = store.peek_token(sample_id)

            async def chunks():
                yield b"sample bytes"

            completed = await store.save_upload(sample_id, chunks())
            download_token = store.peek_token(sample_id)

            self.assertEqual(completed["status"], "complete")
            self.assertNotEqual(upload_token, download_token)
            self.assertFalse(store.verify_token(sample_id, upload_token))
            self.assertTrue(store.verify_token(sample_id, download_token))

            with self.assertRaisesRegex(StoreError, "not pending"):
                await store.save_upload(sample_id, chunks())


if __name__ == "__main__":
    unittest.main()
