//  
#include <nanobind/nanobind.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <vector>

#include "common.hpp"
#include "bind_common.hpp"

namespace nb = nanobind;
using namespace scucse::crypto;
using bind::_toBytes;

// ———  party id → ShrAdd2Pid  mapping  ———

template <int P> struct Add2Party;

template <> struct Add2Party<0>
{
    static constexpr ShrAdd2Pid value = ShrAdd2Pid::P0;
};

template <> struct Add2Party<1>
{
    static constexpr ShrAdd2Pid value = ShrAdd2Pid::P1;
};

// ———  Add2T  template wrapper  ———

template <uint64_t ELL, ShrAdd2Pid PARTY> struct Add2T
{
    static constexpr uint64_t ell = ELL;
    static constexpr int party = static_cast<int>(PARTY);

    using Emp2Type = bind::SHR_ADD2<PARTY, ELL>;
    static_assert(ShrAdd2Cnstr<PARTY, ELL, Emp2>);

    std::shared_ptr<emp::IOChannel> io_;
    std::unique_ptr<Emp2Type> emp2_;

    Add2T() = default;

    /// Construct with a pre-built IOChannel (shared ownership).
    explicit Add2T(std::shared_ptr<emp::IOChannel> io)
        : io_(std::move(io)), emp2_(std::make_unique<Emp2Type>(io_.get()))
    {
    }

    ~Add2T() = default;

    // ——— ring-addition share / recv / send / recv  ———

    uint32_t share(uint32_t value)        { return emp2_->share(value); }
    uint32_t recvShare()                  { return emp2_->recvShare(); }
    void send_scalar(uint32_t val) {
        if (ELL < 32 && val >= (1ULL << ELL))
            throw std::invalid_argument(
                "send_scalar: val out of range for Z_{2^" + std::to_string(ELL) + "}");
        emp2_->send(val);
    }
    uint32_t recv_scalar()               { return emp2_->recv(); }
    void     send_bytes(const uint8_t* data, size_t len) { emp2_->send_data(data, len); }
    void     recv_bytes_(uint8_t* data, size_t len)      { emp2_->recv_data(data, len); }
    uint64_t bytes_sent()     const { return emp2_->bytes_sent(); }
    uint64_t bytes_recv()     const { return emp2_->bytes_recv(); }
    void     clear_send_cnt()       { emp2_->clear_send_cnt(); }
    void     clear_recv_cnt()       { emp2_->clear_recv_cnt(); }

    // ——— XOR share / recv (bytes → bytes, no vector) ———

    std::vector<uint8_t> share_bytes(const uint8_t* data, size_t len) {
        std::vector<uint8_t> v(data, data + len);
        return emp2_->shareBytes(v);
    }
    std::vector<uint8_t> recv_bytes() { return emp2_->recvBytes(); }

    std::vector<uint8_t> share_key(const uint8_t key[16]) {
        return emp2_->shareKey(key);
    }
    std::vector<uint8_t> recv_key() { return emp2_->recvKey(); }

    // ——— circuit operations ———

    uint32_t hash(const uint8_t* pt, size_t ptLen, const uint8_t* key16)
    {
        return emp2_->hash(pt, ptLen, key16);
    }

    uint32_t mod(uint32_t my_a, uint32_t mv)   { return emp2_->mod(my_a, mv); }
    uint32_t equality_test(uint32_t my_a, uint32_t my_b) {
        return emp2_->equalityTest(my_a, my_b);
    }

};

// ———  lookup table  ———

static nb::object add2_types[31 + 1][2]; // [ell][party]

// ———  bind helpers  ———

template <uint64_t ELL, ShrAdd2Pid PARTY> static void bind_add2_instance(nb::module_& m)
{
    using namespace nb::literals;
    using T = Add2T<ELL, PARTY>;
    static constexpr int P = static_cast<int>(PARTY);

    char name[64];
    std::snprintf(name, sizeof(name), "ShrAdd2_%lu_%d", ELL, P);

    nb::class_<T>(m, name)
        .def(
            "__init__",
            [](T* self, nb::object peer_channel) {
                auto io = bind::netio_acquire(
                    nb::cast<uintptr_t>(peer_channel.attr("acquire")()));
                new (self) T(std::move(io));
            },
            "peer_channel"_a,
            "Construct an additive-share party from a persistent channel.\n"
            "  peer_channel  — Channel for communication with the other party."
        )
        .def_prop_ro("ell",   [](const T&) { return ELL; })
        .def_prop_ro("party", [](const T&) { return P; })

        // ——— ring-addition share ———
        .def("share_scalar",       &T::share,     "value"_a)
        .def("recv_scalar_share",  &T::recvShare)
        .def("send_data",          &T::send_scalar, "val"_a, "Send a scalar (uint32_t) to the peer.")
        .def(
            "send_data",
            [](T& self, nb::object data) {
                PyObject* py_buf = data.ptr();
                char* ptr = nullptr;
                Py_ssize_t len = 0;
                if (PyBytes_Check(py_buf)) {
                    ptr = PyBytes_AsString(py_buf);
                    len = PyBytes_Size(py_buf);
                } else if (PyByteArray_Check(py_buf)) {
                    ptr = PyByteArray_AsString(py_buf);
                    len = PyByteArray_Size(py_buf);
                } else {
                    throw std::invalid_argument("send_data: data must be bytes or bytearray");
                }
                self.send_bytes(reinterpret_cast<const uint8_t*>(ptr), len);
            }, "data"_a,
            "Send raw bytes (no length prefix) to the peer."
        )
        .def("recv_data", &T::recv_scalar, "Receive a scalar (uint32_t) from the peer.")
        .def(
            "recv_data",
            [](T& self, nb::object buf) {
                PyObject* py_buf = buf.ptr();
                if (!PyByteArray_Check(py_buf))
                    throw std::invalid_argument("recv_data: buf must be a bytearray");
                char* ptr = PyByteArray_AsString(py_buf);
                Py_ssize_t len = PyByteArray_Size(py_buf);
                self.recv_bytes_(reinterpret_cast<uint8_t*>(ptr), len);
            }, "buf"_a,
            "Receive raw bytes (no length prefix) from the peer into pre-allocated buf."
        )

        // ——— XOR share element (bytes or str → bytes, variable-length with length prefix) ———
        .def("share_element",
             [](T& self, nb::object plain) -> nb::bytes {
                 auto [data, len] = _toBytes(plain);
                 auto v = self.share_bytes(data, len);
                 return nb::bytes(reinterpret_cast<const char*>(v.data()), v.size());
             }, "plain"_a,
             "XOR-share a variable-length element. Sends [len][peer share] (length-prefixed), returns local share as bytes.")
        .def("recv_element_share",
             [](T& self) -> nb::bytes {
                 auto v = self.recv_bytes();
                 return nb::bytes(reinterpret_cast<const char*>(v.data()), v.size());
             },
             "Receive a variable-length XOR element share (reads [len][data]), returns bytes.")

        // ——— XOR share key (16-byte, no length prefix) ———
        .def("share_key",
             [](T& self, nb::bytes key) -> nb::bytes {
                 if (key.size() != 16)
                     throw std::invalid_argument("share_key: key must be exactly 16 bytes");
                 auto v = self.share_key(reinterpret_cast<const uint8_t*>(key.c_str()));
                 return nb::bytes(reinterpret_cast<const char*>(v.data()), v.size());
             }, "key"_a,
             "XOR-share a 16-byte AES key. Sends peer share (no length prefix), returns local share as bytes.")
        .def("recv_key_share",
             [](T& self, nb::object buf) {
                 PyObject* py_buf = buf.ptr();
                 if (!PyByteArray_Check(py_buf))
                     throw std::invalid_argument("recv_key_share: buf must be a bytearray");
                 if (PyByteArray_Size(py_buf) != 16)
                     throw std::invalid_argument("recv_key_share: buf must be exactly 16 bytes");
                 auto v = self.recv_key();
                 std::memcpy(PyByteArray_AsString(py_buf), v.data(), 16);
             }, "buf"_a,
             "Receive a 16-byte AES key share into pre-allocated buf.")

        // ——— circuit operations ———
        .def("hash",
             [](T& self, nb::object my_pt, nb::bytes my_key) {
                 auto [pt, ptLen] = _toBytes(my_pt);
                 return self.hash(pt, ptLen, reinterpret_cast<const uint8_t*>(my_key.c_str()));
             }, "my_pt"_a, "my_key"_a,
             "AES-DM hash in-circuit. preimage and key are XOR shares.\n"
             "  my_pt  — local XOR share of preimage (bytes, ≤16 bytes)\n"
             "  my_key — local XOR share of 128-bit key (exactly 16 bytes)\n"
             "  Returns a ring-addition share (uint32_t) of the ELL-bit output.")
        .def("mod", &T::mod, "my_a"_a, "mv"_a,
             "Modular reduction in-circuit: (a_0+a_1) % mv → share.")
        .def("equality_test", &T::equality_test, "my_a"_a, "my_b"_a,
             "Equality test in-circuit: share of 1 if equal, 0 otherwise.")

        // ——— byte counters ———
        .def("bytes_sent",     &T::bytes_sent,     "Total bytes sent to the peer")
        .def("bytes_recv",     &T::bytes_recv,     "Total bytes received from the peer")
        .def("clear_send_cnt", &T::clear_send_cnt, "Reset sent-byte counter to zero")
        .def("clear_recv_cnt", &T::clear_recv_cnt, "Reset received-byte counter to zero");

    add2_types[ELL][P] = m.attr(name);
}

// ———  module entry  ———

void bind_shr_add2(nb::module_& m)
{
    using namespace nb::literals;

    bind::for_range<bind::ADD2_ELL_MIN, bind::ADD2_ELL_MAX>(
        [&]<uint64_t E>()
        {
            bind_add2_instance<E, ShrAdd2Pid::P0>(m);
            bind_add2_instance<E, ShrAdd2Pid::P1>(m);
        }
    );

    //  Factory: ShrAdd2(ell, party) → class
    m.def(
        "ShrAdd2",
        [](uint64_t ell, int party) -> nb::object
        {
            if (ell < bind::ADD2_ELL_MIN || ell > bind::ADD2_ELL_MAX)
                throw std::invalid_argument(
                    "ell out of range [" + std::to_string(bind::ADD2_ELL_MIN)
                    + ", " + std::to_string(bind::ADD2_ELL_MAX) + "]");
            if (party != 0 && party != 1)
                throw std::invalid_argument("party must be 0 or 1");
            return add2_types[ell][party];
        },
        "ell"_a, "party"_a,
        "Return the ShrAdd2 class for the given (ell, party)."
    );
}
