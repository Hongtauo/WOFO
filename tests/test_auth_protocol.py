"""Synthetic vectors for the ePortal browser's RSA wire format.

The vectors were calculated independently with JavaScript BigInt, following the
16-bit limb format in the original browser RSAUtils implementation.  The small
public key below is test data; it is not a server key or an account credential.

Protocol reference (password + '>' + query MAC before reversal):
https://github.com/Aurorp1g/Auto_Connect/blob/776242341b9304b56165a109cdb7402795ef2e83/post/body.js
"""

import unittest

from core.auth import _rsa_encrypt, encrypt_password


class RSAWireFormatTests(unittest.TestCase):
    # n = 65537 * 65539; e = 17.  A three-limb modulus uses four-byte chunks.
    exponent = "11"
    modulus = "100040003"

    def test_hexadecimal_output_pads_each_16_bit_limb(self):
        self.assertEqual(_rsa_encrypt("a", "1", "ffffffff"), "0061")

    def test_chunk_size_leaves_high_modulus_limb_unused(self):
        self.assertEqual(_rsa_encrypt("abcd", "1", "ffffffff"), "6261 6463")

    def test_leading_zero_in_modulus_does_not_change_chunk_size(self):
        self.assertEqual(_rsa_encrypt("abc", "1", "00ffffffff"), "6261 0063")

    def test_nontrivial_public_exponent_matches_browser_vectors(self):
        vectors = (
            ("a", "3215d6a2"),
            ("abcd", "eb53ed51"),
            ("abcdefgh", "eb53ed51 63b565b3"),
            ("abcdefghi", "eb53ed51 63b565b3 55c944a9"),
            ("!@#_", "63bf644f"),
        )
        for message, expected in vectors:
            with self.subTest(message=message):
                self.assertEqual(
                    _rsa_encrypt(message, self.exponent, self.modulus), expected,
                )

    def test_password_binds_to_redirect_mac_before_reversal(self):
        self.assertEqual(
            encrypt_password("abc", self.exponent, self.modulus, "001122aabbcc"),
            "4d594e58 39b5f9b4 f8e5f9e4 a5a231ef",
        )

    def test_password_separator_is_not_confused_with_binding_separator(self):
        self.assertEqual(
            encrypt_password(
                "synthetic>pass", self.exponent, self.modulus, "001122aabbcc",
            ),
            "4d594e58 39b5f9b4 f8e5f9e4 f811b35c 51b0b18f 6951c211 4bac4960",
        )

    def test_missing_mac_uses_browser_fallback(self):
        self.assertEqual(
            encrypt_password("abc", self.exponent, self.modulus),
            "08dc08dc 08dc08dc b83ac646 3215d6a2",
        )

    def test_empty_mac_uses_browser_fallback(self):
        self.assertEqual(
            encrypt_password("pw", self.exponent, self.modulus, ""),
            "08dc08dc 08dc08dc 19bb098e",
        )


if __name__ == "__main__":
    unittest.main()
