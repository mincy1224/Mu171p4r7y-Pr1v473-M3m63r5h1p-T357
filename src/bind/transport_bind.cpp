//  @author  mincy
//  RingTransport — ring element send/recv over an IOChannel
//
//    ELL 1–8 :  send_vector / recv_vector  +  send_scalar / recv_scalar
//    ELL 9–31:  send_scalar / recv_scalar

#include <nanobind/nanobind.h>

#include <cstdint>
#include <stdexcept>

#include "common.hpp"
#include "bind_common.hpp"

namespace nb = nanobind;
using namespace scucse::crypto;

// ———  transport templates  ————————————————————————————————————————————————

template <uint64_t ELL> struct RingVectorTransport
{
    static_assert(ELL >= 1 && ELL <= 8, "ELL 1-8 for vector transport");
    std::shared_ptr<emp::IOChannel> io_;
    RingVectorTransport(std::shared_ptr<emp::IOChannel> io) : io_(std::move(io)) {}
    static constexpr size_t BYTES = (ELL + 7) / 8;

    void send_vector(const math::Rvector<ELL>& vec, math::RvectorPack& aux_buf)
    {
        packRvec(vec, aux_buf);
        io_->send_data(aux_buf.data(), aux_buf.size());
        io_->flush();
    }
    void recv_vector(math::Rvector<ELL>& vec, math::RvectorPack& aux_buf)
    {
        io_->recv_data(aux_buf.data(), aux_buf.size());
        unpackRvec(aux_buf, vec);
    }

    void send_scalar(uint64_t val)
    {
        uint64_t mask = (UINT64_C(1) << ELL) - 1;
        if (val > mask)
            throw std::invalid_argument(
                "send_scalar(" + std::to_string(val)
                + "): value out of range for Z_{2^" + std::to_string(ELL) + "}");
        uint8_t buf[BYTES];
        for (size_t i = 0; i < BYTES; ++i) buf[i] = static_cast<uint8_t>(val >> (8 * i));
        io_->send_data(buf, BYTES);
        io_->flush();
    }
    uint64_t recv_scalar()
    {
        uint8_t buf[BYTES] = {};
        io_->recv_data(buf, BYTES);
        uint64_t val = 0;
        for (size_t i = 0; i < BYTES; ++i) val |= static_cast<uint64_t>(buf[i]) << (8 * i);
        val &= (UINT64_C(1) << ELL) - 1;
        return val;
    }
};

template <uint64_t ELL> struct RingScalarTransport
{
    static_assert(ELL >= 1 && ELL <= 63, "ELL 1-63 for scalar transport");
    std::shared_ptr<emp::IOChannel> io_;
    RingScalarTransport(std::shared_ptr<emp::IOChannel> io) : io_(std::move(io)) {}
    static constexpr size_t BYTES = (ELL + 7) / 8;

    void send_scalar(uint64_t val)
    {
        if constexpr (ELL < 64) {
            uint64_t mask = (UINT64_C(1) << ELL) - 1;
            if (val > mask)
                throw std::invalid_argument(
                    "send_scalar(" + std::to_string(val)
                    + "): value out of range for Z_{2^" + std::to_string(ELL) + "}");
        }
        uint8_t buf[BYTES];
        for (size_t i = 0; i < BYTES; ++i) buf[i] = static_cast<uint8_t>(val >> (8 * i));
        io_->send_data(buf, BYTES);
        io_->flush();
    }
    uint64_t recv_scalar()
    {
        uint8_t buf[BYTES] = {};
        io_->recv_data(buf, BYTES);
        uint64_t val = 0;
        for (size_t i = 0; i < BYTES; ++i) val |= static_cast<uint64_t>(buf[i]) << (8 * i);
        if constexpr (ELL < 64) { val &= (UINT64_C(1) << ELL) - 1; }
        return val;
    }
};

// ———  bind  —————————————————————————————————————————————————————————

static constexpr int RING_TRANSPORT_MAX = 31;

template <uint64_t ELL> static void bind_ring_transport(nb::module_& m, const char* name)
{
    using namespace nb::literals;

    if constexpr (ELL <= 8)
    {
        using T = RingVectorTransport<ELL>;
        nb::class_<T>(m, name)
            .def("__init__",
                 [](T* self, nb::object channel) {
                     auto io = bind::netio_acquire(
                         nb::cast<uintptr_t>(channel.attr("acquire")()));
                     new (self) T(std::move(io));
                 },
                 "channel"_a, "RingTransport(ell)(channel) — ring element transport.")
            .def_prop_ro("ell", [](const T&) { return ELL; })
            .def("send_vector", &T::send_vector, nb::call_guard<nb::gil_scoped_release>(),
                 "vec"_a, "aux_buf"_a, "Pack vec into aux_buf and send over the channel.")
            .def("recv_vector", &T::recv_vector, nb::call_guard<nb::gil_scoped_release>(),
                 "vec"_a, "aux_buf"_a, "Receive into aux_buf and unpack into vec.")
            .def("send_scalar", &T::send_scalar, nb::call_guard<nb::gil_scoped_release>(),
                 "val"_a, "Send a scalar ring element (ELL bits).")
            .def("recv_scalar", &T::recv_scalar, nb::call_guard<nb::gil_scoped_release>(),
                 "Receive a scalar ring element (ELL bits).");
    }
    else
    {
        using T = RingScalarTransport<ELL>;
        nb::class_<T>(m, name)
            .def("__init__",
                 [](T* self, nb::object channel) {
                     auto io = bind::netio_acquire(
                         nb::cast<uintptr_t>(channel.attr("acquire")()));
                     new (self) T(std::move(io));
                 },
                 "channel"_a, "RingTransport(ell)(channel) — ring element transport.")
            .def_prop_ro("ell", [](const T&) { return ELL; })
            .def("send_scalar", &T::send_scalar, nb::call_guard<nb::gil_scoped_release>(),
                 "val"_a, "Send a scalar ring element (ELL bits).")
            .def("recv_scalar", &T::recv_scalar, nb::call_guard<nb::gil_scoped_release>(),
                 "Receive a scalar ring element (ELL bits).");
    }
}

void bind_transport(nb::module_& m)
{
    using namespace nb::literals;

    //  RingTransportEll1 .. RingTransportEll31  —  per-ELL classes
    static nb::object ring_transport_types[RING_TRANSPORT_MAX + 1];

    bind::for_range<1, RING_TRANSPORT_MAX>(
        [&]<uint64_t ELL>()
        {
            char name[64];
            std::snprintf(name, sizeof(name), "RingTransportEll%lu", ELL);
            bind_ring_transport<ELL>(m, name);
            ring_transport_types[ELL] = m.attr(name);
        }
    );

    //  Factory: RingTransport(ell) → class; class(channel) → instance
    m.def(
        "RingTransport",
        [](uint64_t ell) -> nb::object
        {
            if (ell < 1 || ell > RING_TRANSPORT_MAX)
                throw std::invalid_argument("ell out of range [1, 31]");
            return ring_transport_types[ell];
        },
        "ell"_a,
        "RingTransport(ell)(channel) — ring element transport over a Channel.\n"
        "  ELL 1–8  →  send_vector / recv_vector  +  send_scalar / recv_scalar\n"
        "  ELL 9–31 →  send_scalar / recv_scalar"
    );
}
