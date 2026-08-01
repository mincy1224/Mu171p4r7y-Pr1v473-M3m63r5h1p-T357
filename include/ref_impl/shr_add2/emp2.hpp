//  EMP2 — 2-party additive secret sharing (ADD2).
//
//  @author  mincy
//  @ref     emp-toolkit (https://github.com/emp-toolkit/emp-tool)
#ifndef EMP2_HPP
#define EMP2_HPP

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
        /// @p myPt   local XOR share of the preimage
        /// @p ptLen  preimage byte length (zero-padded to 128 bits in circuit)
        /// @p myKey  local XOR share of the 128-bit key (exactly 16 bytes)
        /// @return ring-addition share of the ELL-bit hash output
        uint32_t hash(const uint8_t* myPt, size_t ptLen, const uint8_t* myKey)
        {
            if (ptLen > 16)
                throw std::invalid_argument(
                    "hash: preimage must be <= 16 bytes, got " + std::to_string(ptLen));
            constexpr size_t N = 128;
            bool pt[N] = {}, ky[N] = {};
            bool zero[N] = {};

            bytesToBits(myPt, ptLen, pt);
            bytesToBits(myKey, 16, ky);

            // P0 inputs their share as ALICE, P1 inputs their share as BOB.
            // The "other" party's input slot is filled with zeros on each side —
            // the OT protocol ensures the real value comes from the owning party.
            auto aw   = sess_->input_bits(emp::ALICE, a() ? pt   : zero, N);
            auto bw   = sess_->input_bits(emp::BOB,   a() ? zero : pt,   N);
            auto ak   = sess_->input_bits(emp::ALICE, a() ? ky   : zero, N);
            auto bk   = sess_->input_bits(emp::BOB,   a() ? zero : ky,   N);

            // XOR-reconstruct in circuit (zero-cost: block XOR)
            std::array<emp::block, N> ptWire, kyWire;
            for (size_t i = 0; i < N; ++i)
            {
                ptWire[i] = aw[i] ^ bw[i];
                kyWire[i] = ak[i] ^ bk[i];
            }

            Blk ptBlk = Blk::from_wires(sess_->ctx(), ptWire.data());
            Blk kyBlk = Blk::from_wires(sess_->ctx(), kyWire.data());

            auto dm = emp::circuit::crypto::aes128_encrypt(sess_->ctx(), ptBlk, kyBlk) ^ ptBlk;
            auto h = dm.template slice<0, ELL>().as_uint();

            // re-share output (ring-addition) for downstream circuit ops
            uint32_t r = rand_mask();
            auto s = sess_->template input<U>(emp::ALICE, r);
            auto v = h - s;
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
