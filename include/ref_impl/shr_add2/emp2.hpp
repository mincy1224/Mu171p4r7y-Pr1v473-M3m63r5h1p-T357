//  EMP2 — 2-party additive secret sharing (ADD2).
//
//  
//  @ref     emp-toolkit (https://github.com/emp-toolkit/emp-tool)
#ifndef EMP2_HPP
#define EMP2_HPP

#include <algorithm>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <vector>

#include <sodium/randombytes.h>
#include "emp-sh2pc/emp-sh2pc.h"
#include "emp-tool/circuits/crypto/aes128.h"
#include "ideal_functionality/shr_add2.hpp"
#include "common.hpp"

namespace scucse::crypto
{

    template <ShrAdd2Pid PARTY, uint64_t ELL>
        requires(ELL >= 1 && ELL <= 31)
    class Emp2
    {
        using Ctx = emp::SH2PCSession::ctx_t;
        using U = emp::UInt_T<Ctx, ELL>;
        using Blk = emp::BitVec_T<Ctx, 128>;

        static constexpr bool a()
        {
            return PARTY == ShrAdd2Pid::P0;
        }
        static constexpr uint32_t share_mask = math::RING_MASK32<ELL>;

    public:
        using ShareType = uint32_t;

        /// Takes a non-owning raw pointer; the caller owns the IOChannel lifetime.
        explicit Emp2(emp::IOChannel* io) : io_(io)
        {
            if (sodium_init() < 0)
                throw std::runtime_error("libsodium init failed");
            if constexpr (a())
            {
                emp::block seed;
                emp::PRG().random_block(&seed, 1);
                io_->send_block(&seed, 1);
            }
            else
            {
                emp::block seed;
                io_->recv_block(&seed, 1);
            }
            io_->flush();
            sess_ = std::make_unique<emp::SH2PCSession>(io_, a() ? emp::ALICE : emp::BOB);
        }

        // ———  public I/O (replaces friend access)  ———

        void send_data(const void* data, size_t len)
        {
            io_->send_data(data, len);
            io_->flush();
        }
        void recv_data(void* data, size_t len)
        {
            io_->recv_data(data, len);
        }

        uint64_t bytes_sent()   const { return io_->send_counter; }
        uint64_t bytes_recv()   const { return io_->recv_counter; }
        void clear_send_cnt()         { io_->send_counter = 0; }
        void clear_recv_cnt()         { io_->recv_counter = 0; }

        // ———  ring share / recv (mod 2^ELL, no circuit)  ———

        uint32_t share(uint32_t value)
        {
            uint32_t r = rand_mask();
            uint32_t complement = math::ringSub32<ELL>(value, r);
            send_data(&complement, sizeof(complement));
            return r;
        }

        uint32_t recvShare()
        {
            uint32_t r;
            recv_data(&r, sizeof(r));
            return r;
        }

        void send(uint32_t val)
        {
            send_data(&val, sizeof(val));
        }

        uint32_t recv()
        {
            uint32_t val;
            recv_data(&val, sizeof(val));
            return val;
        }

        // ———  XOR share / recv (byte array, for hash preimage/key)  ———

        /// XOR-share a byte array.  Sends [uint64_t len][peer share].
        /// Returns the local share.
        std::vector<uint8_t> shareBytes(const std::vector<uint8_t>& plain)
        {
            size_t len = plain.size();
            std::vector<uint8_t> local(len);
            randombytes_buf(local.data(), len);
            std::vector<uint8_t> peer(len);
            for (size_t i = 0; i < len; ++i)
                peer[i] = static_cast<uint8_t>(plain[i] ^ local[i]);
            send_data(&len, sizeof(uint64_t));
            send_data(peer.data(), len);
            return local;
        }

        /// Receive an XOR share.  Reads [uint64_t len][data].
        /// @warning Allocates from network-supplied length without bounds check.
        /// Callers in untrusted environments must add an application-layer limit.
        std::vector<uint8_t> recvBytes()
        {
            uint64_t len = 0;
            recv_data(&len, sizeof(uint64_t));
            std::vector<uint8_t> peer(len);
            recv_data(peer.data(), len);
            return peer;
        }

        /// XOR-share a 16-byte key (no length prefix — fixed 128 bits).
        std::vector<uint8_t> shareKey(const uint8_t key[16])
        {
            std::vector<uint8_t> local(16);
            randombytes_buf(local.data(), 16);
            std::vector<uint8_t> peer(16);
            for (int i = 0; i < 16; ++i)
                peer[i] = static_cast<uint8_t>(key[i] ^ local[i]);
            send_data(peer.data(), 16);
            return local;
        }

        /// Receive a 16-byte key share.
        std::vector<uint8_t> recvKey()
        {
            std::vector<uint8_t> peer(16);
            recv_data(peer.data(), 16);
            return peer;
        }

        // ———  circuit operations  ———

        /// AES-DM hash over XOR-shared preimage and key.
        /// Key must be 16 bytes.  Preimage is arbitrary length — processed
        /// in 16-byte blocks via Davies-Meyer chain:
        ///   H_0 = 0,  H_i = AES(key, H_{i-1} ^ block_i) ^ H_{i-1}.
        /// @p myPt   local XOR share of the preimage
        /// @p ptLen  preimage byte length
        /// @p myKey  local XOR share of the 128-bit key (exactly 16 bytes)
        /// @return ring-addition share of the ELL-bit hash output
        uint32_t hash(const uint8_t* myPt, size_t ptLen, const uint8_t* myKey)
        {
            constexpr size_t BLOCK_BITS  = 128;
            constexpr size_t BLOCK_BYTES = 16;

            size_t nBlocks = (ptLen + BLOCK_BYTES - 1) / BLOCK_BYTES;
            if (nBlocks == 0) nBlocks = 1;

            // ——— preimage bits (nBlocks × 128, last block zero-padded) ———
            size_t N = nBlocks * BLOCK_BITS;
            std::vector<uint8_t> pt_u8(N, 0);
            for (size_t b = 0; b < nBlocks; ++b)
            {
                size_t off  = b * BLOCK_BYTES;
                size_t len  = std::min(ptLen - off, size_t(BLOCK_BYTES));
                bool blockBits[BLOCK_BITS] = {};
                bytesToBits(myPt + off, len, blockBits);
                for (size_t i = 0; i < BLOCK_BITS; ++i)
                    pt_u8[b * BLOCK_BITS + i] = blockBits[i] ? 1 : 0;
            }
            std::vector<uint8_t> zero_u8(N, 0);

            // ——— key bits (always 128) ———
            bool ky[BLOCK_BITS] = {};
            bytesToBits(myKey, 16, ky);

            // ——— input preimage (all blocks) ———
            auto aw = sess_->input_bits(emp::ALICE,
                          a() ? reinterpret_cast<bool*>(pt_u8.data())
                              : reinterpret_cast<bool*>(zero_u8.data()), N);
            auto bw = sess_->input_bits(emp::BOB,
                          a() ? reinterpret_cast<bool*>(zero_u8.data())
                              : reinterpret_cast<bool*>(pt_u8.data()), N);

            // XOR-reconstruct preimage wires
            std::vector<emp::block> ptWire(N);
            for (size_t i = 0; i < N; ++i)
                ptWire[i] = aw[i] ^ bw[i];

            // ——— input key ———
            bool zero128[BLOCK_BITS] = {};
            auto ak = sess_->input_bits(emp::ALICE, a() ? ky    : zero128, BLOCK_BITS);
            auto bk = sess_->input_bits(emp::BOB,   a() ? zero128 : ky,    BLOCK_BITS);
            std::array<emp::block, BLOCK_BITS> kyWire;
            for (size_t i = 0; i < BLOCK_BITS; ++i)
                kyWire[i] = ak[i] ^ bk[i];
            Blk kyBlk = Blk::from_wires(sess_->ctx(), kyWire.data());

            // ——— Davies-Meyer chain ———
            Blk h; // uninitialised — set from first block below
            for (size_t b = 0; b < nBlocks; ++b)
            {
                Blk m = Blk::from_wires(sess_->ctx(),
                                        ptWire.data() + b * BLOCK_BITS);
                if (b == 0)
                {
                    // H_1 = AES(key, 0 ^ m_0) ^ 0 = AES(key, m_0)
                    h = emp::circuit::crypto::aes128_encrypt(sess_->ctx(),
                                                             m, kyBlk);
                }
                else
                {
                    Blk x = h ^ m;  // H_{i-1} ^ block_i
                    auto ct = emp::circuit::crypto::aes128_encrypt(sess_->ctx(),
                                                                   x, kyBlk);
                    h = ct ^ h;     // AES(key, x) ^ H_{i-1}
                }
            }

            auto hashVal = h.template slice<0, ELL>().as_uint();

            // re-share output (ring-addition) for downstream circuit ops
            uint32_t r = rand_mask();
            auto s = sess_->template input<U>(emp::ALICE, r);
            auto v = hashVal - s;
            auto rb = sess_->reveal(v, emp::BOB);

            if constexpr (a()) return r;
            else                return (uint32_t)rb.value() & share_mask;
        }

        uint32_t mod(uint32_t my_a, uint32_t mv)
        {
            if (mv == 0)
                throw std::invalid_argument("mod: mv must not be zero");
            if (mv >= (1ULL << ELL))
                throw std::invalid_argument("mod: mv must be < 2^ELL");
            auto a_a = sess_->template input<U>(emp::ALICE, a() ? my_a : 0);
            auto a_b = sess_->template input<U>(emp::BOB, a() ? 0 : my_a);
            auto r = (a_a + a_b) % U::constant(sess_->ctx(), mv);
            uint32_t t = rand_mask();
            auto s = sess_->template input<U>(emp::ALICE, t);
            auto v = r - s;
            auto rb = sess_->reveal(v, emp::BOB);
            if constexpr (a()) return t;
            else                return (uint32_t)rb.value() & share_mask;
        }

        uint32_t equalityTest(uint32_t my_a, uint32_t my_b)
        {
            auto a_a = sess_->template input<U>(emp::ALICE, a() ? my_a : 0);
            auto a_b = sess_->template input<U>(emp::BOB, a() ? 0 : my_a);
            auto b_a = sess_->template input<U>(emp::ALICE, a() ? my_b : 0);
            auto b_b = sess_->template input<U>(emp::BOB, a() ? 0 : my_b);
            auto diff = (a_a + a_b) - (b_a + b_b);
            auto eq_bit = (diff == U::constant(sess_->ctx(), 0));
            auto eq_val = U::constant(sess_->ctx(), 0).select(eq_bit, U::constant(sess_->ctx(), 1));
            uint32_t r = rand_mask();
            auto s = sess_->template input<U>(emp::ALICE, r);
            auto v = eq_val - s;
            auto rb = sess_->reveal(v, emp::BOB);
            if constexpr (a()) return r;
            else                return (uint32_t)rb.value() & share_mask;
        }

    private:
        static void bytesToBits(const uint8_t* src, size_t nbytes, bool* bits)
        {
            for (size_t i = 0; i < nbytes * 8; ++i)
                bits[i] = (src[i / 8] >> (i % 8)) & 1;
        }

        static uint32_t rand_mask()
        {
            uint32_t v;
            randombytes_buf(&v, 4);
            return v & share_mask;
        }

        emp::IOChannel* io_;
        std::unique_ptr<emp::SH2PCSession> sess_;
    };

} // namespace scucse::crypto
#endif // EMP2_HPP
